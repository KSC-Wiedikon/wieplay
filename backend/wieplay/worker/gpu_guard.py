"""The GPU worker's busy guard (PLAN §8.2): the laptop's GPU is shared, so the worker takes a
job only when nobody else needs the card, and gives way while a job runs.

The Framework 16 also runs the owner's meeting scribe, which loads its speech models onto
the GPU (about 4 GB) while it records or transcribes, and is the development and training
machine. Before every claim the guard checks, in this order:

- a pause file (``touch ~/wieplay-gpu/PAUSE``: the local "wieplay-worker pause" switch; the
  admin page's pause works through the API as for every worker);
- the power: on battery the laptop keeps its charge;
- other processes on the GPU: their memory (``nvidia-smi``'s compute apps, this process
  excluded) above ``gpu_max_other_mib``, or the card busier than ``gpu_max_util`` while
  this worker is idle;
- and, for the job itself, a lock shared by the GPU workers of the machine (dev and prod):
  one job on the card at a time.

While a job runs, the job calls ``yield_while_busy`` between frames: when another process
takes the GPU (or the power goes) it stops feeding the card and waits, so the other work
gets the GPU's time; ONNX Runtime's memory stays capped (``WIEPLAY_WORKER_GPU_MEM_LIMIT_MIB``,
people.py). A pause longer than ``gpu_max_pause_s`` gives the job back to the queue
(RetryLater): the worker's process ends the job, frees the card and claims again later.
Every change of state is logged once with its reason.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

log = logging.getLogger(__name__)

POWER_SUPPLY = Path("/sys/class/power_supply")
CHECK_EVERY_S = 10.0  # inside a job: at most one nvidia-smi call per this many seconds
WAIT_STEP_S = 30.0  # before a claim, or while paused in a job


class GpuBusy(Exception):
    """Paused longer than allowed inside a job: give it back to the queue."""

    def __init__(self, reason: str, seconds: int) -> None:
        super().__init__(reason)
        self.seconds = seconds


@dataclass
class GpuApp:
    pid: int
    name: str
    mib: int


@dataclass
class GpuState:
    util: int | None = None  # percent, the whole card
    used_mib: int | None = None
    total_mib: int | None = None
    apps: list[GpuApp] = field(default_factory=list)
    error: str | None = None


def _smi(args: list[str]) -> list[list[str]]:
    out = subprocess.run(  # noqa: S603 - fixed argument list
        ["nvidia-smi", *args, "--format=csv,noheader,nounits"],  # noqa: S607 - from PATH
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    return [
        [c.strip() for c in line.split(",")] for line in out.stdout.splitlines() if line.strip()
    ]


def _int(text: str) -> int | None:
    try:
        return int(float(text))
    except ValueError:
        return None  # "[N/A]"


def read_gpu() -> GpuState:
    """The card's load and the processes on it, from nvidia-smi (comes with the driver)."""
    state = GpuState()
    try:
        rows = _smi(["--query-gpu=utilization.gpu,memory.used,memory.total"])
        if rows:
            state.util, state.used_mib, state.total_mib = (_int(v) for v in rows[0][:3])
        for row in _smi(["--query-compute-apps=pid,process_name,used_memory"]):
            if len(row) >= 3 and (pid := _int(row[0])) is not None:
                name = row[1] if row[1].startswith("[") else row[1].rsplit("/", 1)[-1]
                state.apps.append(GpuApp(pid, name, _int(row[2]) or 0))
    except (OSError, subprocess.SubprocessError) as exc:
        state.error = str(exc)[:200]
    return state


def on_mains(root: Path = POWER_SUPPLY) -> bool:
    """True on the charger (or a machine without a battery), False on battery."""
    if not root.is_dir():
        return True
    battery = False
    for supply in root.iterdir():
        try:
            kind = (supply / "type").read_text().strip()
        except OSError:
            continue
        if kind == "Battery":
            battery = True
            continue
        try:
            if (supply / "online").read_text().strip() == "1":
                return True
        except OSError:
            continue
    return not battery


@dataclass
class GuardSettings:
    max_other_mib: int = 1024
    max_util: int = 40
    need_mains: bool = True
    pause_file: Path | None = None
    max_pause_s: int = 1200
    retry_after_s: int = 1800
    # Shared by the GPU workers of one machine (dev and prod): one job on the card at a time.
    lock_file: Path | None = None


class GpuGuard:
    def __init__(
        self,
        settings: GuardSettings,
        *,
        read: Callable[[], GpuState] = read_gpu,
        mains: Callable[[], bool] = on_mains,
        own_pid: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._read, self._mains, self._clock = read, mains, clock
        self.own_pid = own_pid if own_pid is not None else os.getpid()
        self._last_reason: str | None = "starting"
        self._last_check = -1e9
        self._busy_reason: str | None = None
        self._lock: IO[str] | None = None
        self.paused_s = 0.0  # inside the current job

    def reason(self, *, in_job: bool = False) -> str | None:
        """Why the worker must not use the GPU now; None when it may."""
        s = self.settings
        if s.pause_file is not None and s.pause_file.exists():
            return f"paused by {s.pause_file}"
        if s.need_mains and not self._mains():
            return "on battery"
        state = self._read()
        if state.error:
            return f"cannot read the GPU: {state.error}"
        others = [a for a in state.apps if a.pid != self.own_pid]
        other_mib = sum(a.mib for a in others)
        if other_mib > s.max_other_mib:
            names = ", ".join(sorted({f"{a.name} ({a.mib} MiB)" for a in others}))
            return f"other processes hold {other_mib} MiB of GPU memory: {names}"
        # The card's load is only someone else's while this worker runs nothing.
        if not in_job and state.util is not None and state.util > s.max_util:
            return f"the GPU is {state.util} % busy"
        return None

    def _log(self, reason: str | None) -> None:
        if reason == self._last_reason:
            return
        if reason is None:
            log.info("GPU free: claiming jobs")
        else:
            log.info("GPU guard: waiting (%s)", reason)
        self._last_reason = reason

    def wait_until_free(self, stopping: threading.Event) -> bool:
        """Before a claim: block while the GPU is not ours to use. False when stopping."""
        while not stopping.is_set():
            reason = self.reason()
            self._log(reason)
            if reason is None:
                return True
            stopping.wait(WAIT_STEP_S)
        return False

    def start_job(self) -> None:
        self.paused_s = 0.0
        self._last_check = -1e9

    def lock(self, stop: threading.Event) -> bool:
        """Hold the machine's GPU lock for a job (another worker on this machine may hold it:
        wait). False when asked to stop first."""
        path = self.settings.lock_file
        if path is None:
            return True
        import fcntl

        handle = path.open("a+")
        logged = False
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._lock = handle
                return True
            except BlockingIOError:
                if not logged:
                    log.info("GPU guard: another worker on this machine runs a job; waiting")
                    logged = True
                if stop.wait(10):
                    handle.close()
                    return False

    def unlock(self) -> None:
        handle = self._lock
        if handle is not None:
            handle.close()  # releases the lock
            self._lock = None

    def yield_while_busy(
        self, cancel: threading.Event, report: Callable[[str], None] | None = None
    ) -> None:
        """Inside a job, between frames: returns at once when the GPU is free (checked every
        CHECK_EVERY_S), else waits until it is. Raises GpuBusy after max_pause_s."""
        now = self._clock()
        if now - self._last_check < CHECK_EVERY_S:
            return
        self._last_check = now
        reason = self.reason(in_job=True)
        if reason is None:
            return
        started = now
        log.info("GPU guard: job paused (%s)", reason)
        while reason is not None and not cancel.is_set():
            waited = self._clock() - started
            if waited > self.settings.max_pause_s:
                self.paused_s += waited
                raise GpuBusy(
                    f"GPU busy for {round(waited)} s ({reason}): job given back",
                    self.settings.retry_after_s,
                )
            if report is not None:
                report(f"paused: {reason}")
            cancel.wait(WAIT_STEP_S)
            reason = self.reason(in_job=True)
        self.paused_s += self._clock() - started
        self._last_check = self._clock()
        log.info("GPU guard: job resumed after %.0f s", self._clock() - started)
