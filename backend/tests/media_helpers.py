"""Helpers shared by the video and tagging tests: personas, uploads, a fake worker run."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from nomal.api.dev_fixtures import personas
from tests.fakes import FakeStore

TODAY = datetime.now(UTC).date()
P = personas(TODAY)
H3, D1, DU23, HERREN1 = 9101, 9102, 9103, 9104
PART = 64 * 1024 * 1024
PLAYLIST = (
    "#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:4\n#EXT-X-PLAYLIST-TYPE:VOD\n"
    '#EXT-X-MAP:URI="init.mp4"\n#EXTINF:4.000000,\nseg_00000.m4s\n#EXTINF:4.000000,\n'
    "seg_00001.m4s\n#EXT-X-ENDLIST\n"
)


def game_id(team: int, k: int) -> int:
    """Fixture games: team*100+k, played weekly from 21 days ago (k=3 is today)."""
    return team * 100 + k


def worker_client(app: FastAPI, admin: TestClient, caps: list[str]) -> TestClient:
    name = f"w-{len(caps)}-{uuid.uuid4().hex[:6]}"
    created = admin.post("/api/admin/workers", json={"name": name, "caps": caps})
    assert created.status_code == 201, created.text
    w = TestClient(app, follow_redirects=False)
    w.headers["Authorization"] = f"Bearer {created.json()['token']}"
    return w


def create_match(client: TestClient, sizes: tuple[int, ...] = (PART + 10,)) -> Any:
    return client.post(
        f"/api/teams/{H3}/videos",
        json={
            "kind": "match",
            "game_id": game_id(H3, 1),
            "files": [
                {"name": f"C000{n}.MP4", "size": size, "type": "video/mp4"}
                for n, size in enumerate(sizes, 1)
            ],
        },
    )


def upload_all(client: TestClient, store: FakeStore, video: dict[str, Any]) -> dict[str, Any]:
    for f in video["files"]:
        parts = list(range(1, f["part_count"] + 1))
        signed = client.post(
            f"/api/videos/{video['id']}/files/{f['id']}/parts", json={"parts": parts}
        )
        assert signed.status_code == 200, signed.text
        for p in signed.json():
            upload_id = p["url"].split("uploadId=")[1].split("&")[0]
            size = min(PART, f["size"] - (p["part"] - 1) * PART)
            store.put_part(upload_id, p["part"], size)
        done = client.post(f"/api/videos/{video['id']}/files/{f['id']}/complete")
        assert done.status_code == 200, done.text
        video = done.json()
    return video


def claim(w: TestClient, caps: list[str]) -> list[dict[str, Any]]:
    r = w.post("/api/worker/claim", json={"caps": caps, "wait": False})
    assert r.status_code == 200, r.text
    jobs: list[dict[str, Any]] = r.json()["jobs"]
    return jobs


def ingest_result(job: dict[str, Any], height: int = 1080, fps: int = 50) -> dict[str, Any]:
    return {
        "files": [
            {
                "file_id": f["id"],
                "sha256": "a" * 64,
                "nas_path": f"/nas/nomal/raw/{job['params']['raw_subdir']}/{f['part_no']:02d}.mp4",
                "duration_ms": 3_600_000,
                "probe": {"width": height * 16 // 9, "height": height},
            }
            for f in job["params"]["files"]
        ],
        "duration_ms": 3_600_000 * len(job["params"]["files"]),
        "width": height * 16 // 9,
        "height": height,
        "fps_num": fps,
        "fps_den": 1,
        "has_audio": True,
        "capture": {"codec": "h264"},
    }


def transcode_result(job: dict[str, Any]) -> dict[str, Any]:
    spec = job["params"]["rendition"]
    return {
        "name": spec["name"],
        "codecs": "avc1.64002a,mp4a.40.2",
        "width": spec["height"] * 16 // 9,
        "height": spec["height"],
        "fps": job["params"]["fps"],
        "bandwidth": 6_000_000,
        "segment_seconds": 4,
        "segment_count": 2,
        "bytes": 4_000_000,
        "playlist": PLAYLIST,
    }


THUMBS = {
    "interval_s": 10, "cols": 10, "rows": 10, "tile_w": 160, "tile_h": 90,
    "count": 720, "sheets": 8, "poster": True,
}  # fmt: skip


def run_pipeline(w: TestClient, caps: list[str], store: FakeStore) -> None:
    [ingest] = claim(w, caps)
    assert ingest["kind"] == "ingest"
    staged = [f["object_key"] for f in ingest["params"]["files"]]
    assert (
        w.post(
            f"/api/worker/jobs/{ingest['id']}/sign", json={"op": "get", "keys": staged}
        ).status_code
        == 200
    )
    assert (
        w.post(
            f"/api/worker/jobs/{ingest['id']}/sign", json={"op": "delete", "keys": staged}
        ).status_code
        == 200
    )
    assert not any(k in store.objects for k in staged)
    assert (
        w.post(f"/api/worker/jobs/{ingest['id']}/complete", json=ingest_result(ingest)).status_code
        == 204
    )
    while jobs := claim(w, caps):
        job = jobs[0]
        if job["kind"] == "transcode":
            body = transcode_result(job)
        else:
            assert job["kind"] == "thumbs"
            body = THUMBS
        r = w.post(f"/api/worker/jobs/{job['id']}/complete", json=body)
        assert r.status_code == 204, r.text


MEDIA_CAPS = ["ingest", "transcode", "thumbs", "nas"]
