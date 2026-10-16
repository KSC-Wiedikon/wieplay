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
from nomal.worker.handlers import HANDLERS, Context, PermanentError, RetryLater
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
    def __init__(self, settings: WorkerSettings) -> None:
        self.settings = settings
        self.api = ApiClient(settings.api_url, settings.resolved_token(), __version__)
        self.stopping = threading.Event()
        self.inbox = (
            InboxScanner(settings, self.api)
            if "inbox" in settings.caps and settings.inbox_root
            else None
        )

    def stop(self, *_: object) -> None:
        log.info("stopping after the current job")
        self.stopping.set()

    def run_job(self, job: ClaimedJob) -> None:
        handler = HANDLERS.get(job.kind)
        log.info("job %s: %s (attempt %d)", job.id, job.kind, job.attempt)
        if handler is None:
            self.api.fail(job.id, f"this worker cannot run {job.kind}", retryable=True)
            return
        cancel = threading.Event()
        started = time.monotonic()
        with _Heartbeat(self.api, job.id, self.settings.heartbeat_seconds, cancel) as beat:
            ctx = Context(self.settings, self.api, job, cancel, beat.update)
            try:
                result = handler(ctx)
                self.api.complete(job.id, result)
                log.info("job %s done in %.0fs", job.id, time.monotonic() - started)
            except (ffmpeg.Cancelled, NotLeased):
                log.warning("job %s stopped (cancelled or lease lost)", job.id)
            except RetryLater as exc:
                log.warning("job %s waits %d s: %s", job.id, exc.seconds, exc)
                self._fail(job, str(exc), retryable=True, retry_after_s=exc.seconds)
            except (PermanentError, ffmpeg.FfmpegError) as exc:
                log.error("job %s failed: %s", job.id, exc)
                self._fail(job, str(exc), retryable=isinstance(exc, ffmpeg.FfmpegError))
            except Exception as exc:
                log.exception("job %s crashed", job.id)
                self._fail(job, f"{type(exc).__name__}: {exc}", retryable=True)
            finally:
                shutil.rmtree(self.settings.work_dir / job.id, ignore_errors=True)

    def _fail(
        self, job: ClaimedJob, error: str, *, retryable: bool, retry_after_s: int | None = None
    ) -> None:
        try:
            self.api.fail(job.id, error, retryable=retryable, retry_after_s=retry_after_s)
        except (ApiUnavailable, NotLeased):
            log.warning("could not report the failure of %s; the lease will expire", job.id)

    def run(self) -> None:
        self.settings.work_dir.mkdir(parents=True, exist_ok=True)
        if self.inbox is not None:
            self.inbox.start()
        caps = self.settings.caps
        log.info(
            "worker %s up: caps %s, encoder %s", __version__, ",".join(caps), self.settings.encoder
        )
        delay = 5.0
        while not self.stopping.is_set():
            try:
                jobs, paused = self.api.claim(caps)
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
                self.run_job(job)
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
