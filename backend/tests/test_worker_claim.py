"""A worker's claim when the worker goes away during it (PLAN §8.1).

On dev, right after the worker was replaced, a training job was leased within 0.1 s of being
queued and then sat for six and a half minutes until its lease ran out: the replaced worker's
last long poll was still polling at the API, because the HTTP middleware hid from the route
that its client was gone. These drive the API as an ASGI app with a client that leaves right
after sending its claim, which TestClient cannot do.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.types import Message

from wieplay.api import worker as worker_api
from wieplay.core import jobs
from wieplay.core.models import Job, Worker

CAPS = ["analyse", "nas"]


@pytest.fixture(autouse=True)
def quick_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_api, "LONG_POLL_SECONDS", 3.0)
    monkeypatch.setattr(worker_api, "POLL_EVERY_SECONDS", 0.05)


def new_worker(app: FastAPI) -> str:
    token, token_hash = jobs.new_worker_token()
    with app.state.sessionmaker() as db:
        db.add(Worker(id=uuid.uuid4(), name=f"w-{token[-6:]}", caps=CAPS, token_hash=token_hash))
        db.commit()
    return token


def queue_job(app: FastAPI, *, runnable_in_s: float = 0.0) -> uuid.UUID:
    not_before = datetime.now(UTC) + timedelta(seconds=runnable_in_s) if runnable_in_s else None
    with app.state.sessionmaker() as db:
        job = jobs.enqueue(db, "train_rallies", caps=CAPS, not_before=not_before)
        db.commit()
        return job.id


def job_row(app: FastAPI, job_id: uuid.UUID) -> Job:
    with app.state.sessionmaker() as db:
        job = db.get(Job, job_id)
        assert job is not None
        db.expunge(job)
        return job


async def _claim(app: FastAPI, token: str, *, leaves: bool) -> tuple[dict[str, Any], float]:
    """POST /api/worker/claim; `leaves`: the worker is gone right after sending it."""
    raw = json.dumps({"caps": CAPS, "wait": True}).encode()
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": raw, "more_body": False}
        if leaves:
            return {"type": "http.disconnect"}
        await asyncio.Event().wait()  # still there: nothing more to read
        raise AssertionError("unreachable")

    out: list[Message] = []

    async def send(message: Message) -> None:
        out.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/worker/claim",
        "raw_path": b"/api/worker/claim",
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"host", b"testserver"),
            (b"authorization", f"Bearer {token}".encode()),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(raw)).encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    started = time.monotonic()
    await app(scope, receive, send)
    elapsed = time.monotonic() - started
    [start] = [m for m in out if m["type"] == "http.response.start"]
    assert start["status"] == 200
    body = b"".join(m.get("body", b"") for m in out if m["type"] == "http.response.body")
    return json.loads(body), elapsed


def claim(app: FastAPI, token: str, *, leaves: bool) -> tuple[dict[str, Any], float]:
    return asyncio.run(_claim(app, token, leaves=leaves))


def test_a_claim_whose_worker_went_away_stops_polling_and_leases_nothing(app: FastAPI) -> None:
    token = new_worker(app)
    # Appears after the worker has gone, well before the long poll ends (3 s here, 25 s in
    # production): a claim that noticed nothing would lease it. 2 s leaves room for a slow CI
    # runner (0.8-0.9 s seen for the request itself).
    job_id = queue_job(app, runnable_in_s=2.0)
    answer, elapsed = claim(app, token, leaves=True)
    assert answer["jobs"] == []
    assert elapsed < 2.0
    job = job_row(app, job_id)
    assert (job.status, job.lease_owner, job.attempts) == ("queued", None, 0)


def test_jobs_leased_as_the_worker_went_away_go_back_at_once(app: FastAPI) -> None:
    token = new_worker(app)
    job_id = queue_job(app)  # runnable at once: the first poll leases it
    answer, _ = claim(app, token, leaves=True)
    assert answer["jobs"] == []
    job = job_row(app, job_id)
    # Back in the queue without waiting for the lease, and the attempt does not count.
    assert (job.status, job.lease_owner, job.attempts, job.not_before) == ("queued", None, 0, None)


def test_a_worker_that_stays_gets_its_job(app: FastAPI) -> None:
    token = new_worker(app)
    job_id = queue_job(app, runnable_in_s=0.2)
    answer, _ = claim(app, token, leaves=False)
    assert [j["id"] for j in answer["jobs"]] == [str(job_id)]
    assert job_row(app, job_id).status == "running"
