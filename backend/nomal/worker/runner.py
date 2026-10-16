"""The worker loop: claim, run with heartbeats, report (PLAN §8.1)."""

from __future__ import annotations

import logging
import shutil
import signal
import threading
import time
from types import FrameType

from nomal import __version__
from nomal.worker import ffmpeg
from nomal.worker.client import ApiClient, ApiUnavailable, ClaimedJob, NotLeased
from nomal.worker.config import WorkerSettings
from nomal.worker.handlers import (
    BACKGROUND_KINDS,
    HANDLERS,
    Context,
    PermanentError,
    RetryLater,
)
from nomal.worker.inbox import InboxScanner

log = logging.getLogger(__name__)


class _Heartbeat:
    """Sends progress every few seconds; sets `cancel` when the API says stop."""

    def __init__(self, api: ApiClient, job_id: str, every: int, cancel: threading.Event) -> None:
        self.api, self.job_id, self.every, self.cancel = api, job_id, every, cancel
        self.progress: float | None = 0.0
        self.message: str | None = "started"
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def update(self, progress: float, message: str) -> None:
        self.progress, self.message = progress, message

    def _run(self) -> None:
        while not self._stop.wait(self.every):
            if self.api.heartbeat(self.job_id, self.progress, self.message):
                log.warning("job %s cancelled by the API", self.job_id)
                self.cancel.set()
                return

    def __enter__(self) -> _Heartbeat:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


class Worker:
    """Claims and runs jobs. With the "analyse" capability it has two lanes: the main one
    for everything that leads to playback, and a background one for the long analysis
    jobs, so an hour of analysis never holds up an upload (PLAN §10.1)."""

    def __init__(self, settings: WorkerSettings) -> None:
        self.settings = settings
        self.api = ApiClient(settings.api_url, settings.resolved_token(), __version__)
        self.stopping = threading.Event()
        # Set once the API has heard "restarted" (and freed our old leases): only then may
        # the background lane claim, or the main lane's first claim would free its job.
        self.released = threading.Event()
        self.inbox = (
            InboxScanner(settings, self.api)
            if "inbox" in settings.caps and settings.inbox_root
            else None
        )
        caps = list(settings.caps)
        if "analyse" in caps and settings.analysis_root is None:
            log.error("the analyse capability needs NOMAL_WORKER_ANALYSIS_ROOT; not analysing")
            caps.remove("analyse")
        self.main_caps = [c for c in caps if c != "analyse"]
        self.background_caps = [c for c in caps if c in ("analyse", "nas")]
        if "analyse" not in caps:
            self.background_caps = []

    def stop(self, *_: object) -> None:
        log.info("stopping after the current job")
        self.stopping.set()

    def run_job(self, job: ClaimedJob, api: ApiClient | None = None) -> None:
        api = api or self.api
        handler = HANDLERS.get(job.kind)
        log.info("job %s: %s (attempt %d)", job.id, job.kind, job.attempt)
        if handler is None:
            api.fail(job.id, f"this worker cannot run {job.kind}", retryable=True)
            return
        cancel = threading.Event()
        started = time.monotonic()
        with _Heartbeat(api, job.id, self.settings.heartbeat_seconds, cancel) as beat:
            ctx = Context(self.settings, api, job, cancel, beat.update)
            try:
                result = handler(ctx)
                api.complete(job.id, result)
                log.info("job %s done in %.0fs", job.id, time.monotonic() - started)
            except (ffmpeg.Cancelled, NotLeased):
                log.warning("job %s stopped (cancelled or lease lost)", job.id)
            except RetryLater as exc:
                log.warning("job %s waits %d s: %s", job.id, exc.seconds, exc)
                self._fail(api, job, str(exc), retryable=True, retry_after_s=exc.seconds)
            except (PermanentError, ffmpeg.FfmpegError) as exc:
                log.error("job %s failed: %s", job.id, exc)
                self._fail(api, job, str(exc), retryable=isinstance(exc, ffmpeg.FfmpegError))
            except Exception as exc:
                log.exception("job %s crashed", job.id)
                self._fail(api, job, f"{type(exc).__name__}: {exc}", retryable=True)
            finally:
                shutil.rmtree(self.settings.work_dir / job.id, ignore_errors=True)

    def _fail(
        self,
        api: ApiClient,
        job: ClaimedJob,
        error: str,
        *,
        retryable: bool,
        retry_after_s: int | None = None,
    ) -> None:
        try:
            api.fail(job.id, error, retryable=retryable, retry_after_s=retry_after_s)
        except (ApiUnavailable, NotLeased):
            log.warning("could not report the failure of %s; the lease will expire", job.id)

    def _lane(
        self, api: ApiClient, caps: list[str], kinds: list[str] | None, *, first: bool
    ) -> None:
        delay = 5.0
        restarted = first  # until the API has heard it once
        if not first:
            while not self.released.wait(5):
                if self.stopping.is_set():
                    return
        while not self.stopping.is_set():
            try:
                jobs, paused = api.claim(caps, restarted=restarted, kinds=kinds)
                if restarted:
                    restarted = False
                    self.released.set()
                delay = 5.0
            except (ApiUnavailable, NotLeased) as exc:
                log.warning("claim failed: %s (retrying in %.0fs)", exc, delay)
                self.stopping.wait(delay)
                delay = min(delay * 2, 300)
                continue
            if paused:
                self.stopping.wait(30)
                continue
            for job in jobs:
                self.run_job(job, api)

    def run(self) -> None:
        self.settings.work_dir.mkdir(parents=True, exist_ok=True)
        if self.inbox is not None:
            self.inbox.start()
        log.info(
            "worker %s up: caps %s, encoder %s%s",
            __version__,
            ",".join(self.main_caps),
            self.settings.encoder,
            f"; background lane {','.join(self.background_caps)}" if self.background_caps else "",
        )
        background = None
        if self.background_caps:
            lane_api = ApiClient(self.settings.api_url, self.settings.resolved_token(), __version__)
            background = threading.Thread(
                target=self._lane,
                args=(lane_api, self.background_caps, list(BACKGROUND_KINDS)),
                kwargs={"first": False},
                name="background-lane",
                daemon=True,
            )
            background.start()
        self._lane(self.api, self.main_caps, None, first=True)
        if background is not None:
            background.join(timeout=10)
        if self.inbox is not None:
            self.inbox.stop()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    worker = Worker(WorkerSettings())

    def on_signal(signum: int, frame: FrameType | None) -> None:
        worker.stop()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    worker.run()
