"""HTTP client for the worker endpoints (PLAN §8.1)."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)


class ApiUnavailable(Exception):
    pass


class NotLeased(Exception):
    """The API no longer considers this job ours (cancelled, reaped)."""


@dataclass(frozen=True)
class ClaimedJob:
    id: str
    kind: str
    video_id: str | None
    params: dict[str, Any]
    attempt: int
    checkpoint: dict[str, Any] | None


class ApiClient:
    def __init__(self, base_url: str, token: str, version: str) -> None:
        self.version = version
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + "/api/worker",
            headers={"Authorization": f"Bearer {token}", "User-Agent": f"nomal-worker/{version}"},
            timeout=httpx.Timeout(60.0, connect=10.0),
        )

    def _post(self, path: str, body: Any, *, attempts: int = 5) -> httpx.Response:
        delay = 2.0
        for attempt in range(1, attempts + 1):
            try:
                response = self._http.post(path, json=body)
            except httpx.TransportError as exc:
                if attempt == attempts:
                    raise ApiUnavailable(str(exc)) from exc
                log.warning("API unreachable (%s); retrying in %.0fs", exc, delay)
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            if response.status_code >= 500 and attempt < attempts:
                log.warning("API answered %s; retrying in %.0fs", response.status_code, delay)
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            if response.status_code == 409:
                raise NotLeased(response.text)
            if response.status_code >= 400:
                raise ApiUnavailable(f"{path}: {response.status_code} {response.text[:300]}")
            return response
        raise ApiUnavailable(path)

    def claim(self, caps: Sequence[str], *, wait: bool = True) -> tuple[list[ClaimedJob], bool]:
        response = self._post(
            "/claim", {"caps": list(caps), "max": 1, "version": self.version, "wait": wait}
        )
        body = response.json()
        jobs = [
            ClaimedJob(
                id=j["id"],
                kind=j["kind"],
                video_id=j.get("video_id"),
                params=j["params"],
                attempt=j["attempt"],
                checkpoint=j.get("checkpoint"),
            )
            for j in body["jobs"]
        ]
        return jobs, bool(body.get("paused"))

    def heartbeat(self, job_id: str, progress: float | None, message: str | None) -> bool:
        """Returns True when the API wants the job stopped."""
        try:
            response = self._post(
                f"/jobs/{job_id}/heartbeat", {"progress": progress, "message": message}, attempts=2
            )
        except ApiUnavailable:
            return False  # keep working; the lease outlives a short outage
        return bool(response.json().get("cancel"))

    def sign(self, job_id: str, op: str, keys: Sequence[str]) -> dict[str, str]:
        urls: dict[str, str] = {}
        for start in range(0, len(keys), 500):
            response = self._post(
                f"/jobs/{job_id}/sign", {"op": op, "keys": list(keys[start : start + 500])}
            )
            urls.update(response.json()["urls"])
        return urls

    def complete(self, job_id: str, result: dict[str, Any]) -> None:
        self._post(f"/jobs/{job_id}/complete", result)

    def fail(
        self, job_id: str, error: str, *, retryable: bool, retry_after_s: int | None = None
    ) -> None:
        body: dict[str, Any] = {"error": error[:4000], "retryable": retryable}
        if retry_after_s is not None:
            body["retry_after_s"] = retry_after_s
        self._post(f"/jobs/{job_id}/fail", body)

    def report_inbox(self, files: list[dict[str, Any]]) -> None:
        self._post("/inbox", {"files": files})
