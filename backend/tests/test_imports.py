"""Imports from YouTube and Twitch links (PLAN §7.2.2)."""

# ruff: noqa: F811  (pytest fixtures imported from test_videos)

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nomal.core import imports
from nomal.worker import importer
from tests.fakes import FakeStore
from tests.test_videos import (  # noqa: F401  (fixtures)
    H3,
    MEDIA_CAPS,
    THUMBS,
    as_,
    claim,
    club,
    game_id,
    ingest_result,
    store,
    transcode_result,
    worker_client,
)

LUCA = {  # what yt-dlp says about a video of Luca Zbinden's channel
    "channel": "Luca Zbinden Volley",
    "channel_id": "UC1j3b8R-P4IROFGWWPvlllA",
    "channel_url": "https://www.youtube.com/channel/UC1j3b8R-P4IROFGWWPvlllA",
    "uploader": "Luca Zbinden Volley",
    "uploader_id": "@lucazbindenvolley2500",
    "uploader_url": "https://www.youtube.com/@lucazbindenvolley2500",
    "title": "Herren Volleyball 2. Liga SVRZ Heimspiel Volero Legends 2026/27",
    "upload_date": "20261003",
    "live_status": "not_live",
}
IMPORT_CAPS = [*MEDIA_CAPS, "import"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://www.youtube.com/watch?v=NXNsZ_UNM6w", ("youtube", "NXNsZ_UNM6w")),
        ("youtube.com/watch?v=NXNsZ_UNM6w&t=120s&si=x", ("youtube", "NXNsZ_UNM6w")),
        ("https://m.youtube.com/watch?v=NXNsZ_UNM6w", ("youtube", "NXNsZ_UNM6w")),
        ("https://youtu.be/NXNsZ_UNM6w?si=abc", ("youtube", "NXNsZ_UNM6w")),
        ("https://www.youtube.com/live/NXNsZ_UNM6w", ("youtube", "NXNsZ_UNM6w")),
        ("https://www.twitch.tv/videos/2245678901", ("twitch", "2245678901")),
        (" https://twitch.tv/videos/2245678901/ ", ("twitch", "2245678901")),
    ],
)
def test_video_links_are_parsed_to_a_canonical_address(
    text: str, expected: tuple[str, str]
) -> None:
    got = imports.parse_link(text)
    assert (got.platform, got.external_id) == expected
    assert imports.parse_link(got.url) == got  # the worker re-parses what it fetches


@pytest.mark.parametrize(
    "text",
    [
        "https://www.youtube.com/@lucazbindenvolley2500",  # a channel, not a video
        "https://www.youtube.com/playlist?list=PL123",
        "https://www.youtube.com/watch?v=NXNsZ_UNM6w&v=AAAAAAAAAAA",  # two ids
        "https://www.youtube.com.evil.example/watch?v=NXNsZ_UNM6w",
        "https://user@www.youtube.com/watch?v=NXNsZ_UNM6w",
        "https://www.youtube.com:8443/watch?v=NXNsZ_UNM6w",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://169.254.169.254/latest/meta-data/",
        "https://www.twitch.tv/lucazbinden",  # a channel
        "https://clips.twitch.tv/SomeClip",
        "https://vimeo.com/123456",
        "https://www.youtube.com/watch?v=NXNsZ_UNM6w extra",
    ],
)
def test_anything_else_is_refused(text: str) -> None:
    with pytest.raises(imports.NotImportable):
        imports.parse_link(text)


def test_channels_and_matching() -> None:
    assert imports.parse_channel("@LucaZbindenVolley2500") == ("youtube", "lucazbindenvolley2500")
    assert imports.parse_channel("https://www.youtube.com/@lucazbindenvolley2500/videos") == (
        "youtube",
        "lucazbindenvolley2500",
    )
    assert imports.parse_channel("UC1j3b8R-P4IROFGWWPvlllA") == (
        "youtube",
        "UC1j3b8R-P4IROFGWWPvlllA",
    )
    assert imports.parse_channel("twitch.tv/KSC_Wiedikon") == ("twitch", "ksc_wiedikon")
    with pytest.raises(imports.NotImportable):
        imports.parse_channel("https://www.twitch.tv/videos/123456")
    assert imports.channel_allowed("youtube", LUCA, {"lucazbindenvolley2500"})
    assert imports.channel_allowed("youtube", LUCA, {"UC1j3b8R-P4IROFGWWPvlllA"})
    assert not imports.channel_allowed("youtube", LUCA, {"someoneelse"})
    assert imports.channel_allowed("twitch", {"uploader_id": "KSC_Wiedikon"}, {"ksc_wiedikon"})


def test_admins_keep_the_allow_list(club: dict[str, TestClient], as_: Any) -> None:
    admin = club["admin"]
    added = admin.post(
        "/api/admin/import-channels",
        json={"channel": "https://www.youtube.com/@lucazbindenvolley2500", "label": "Luca"},
    )
    assert added.status_code == 201, added.text
    assert added.json()["ref"] == "lucazbindenvolley2500"
    again = admin.post(
        "/api/admin/import-channels", json={"channel": "@LucaZbindenVolley2500", "label": "x"}
    )
    assert again.status_code == 409
    bad = admin.post("/api/admin/import-channels", json={"channel": "hello there", "label": "x"})
    assert bad.status_code == 422
    assert [c["label"] for c in admin.get("/api/admin/import-channels").json()] == ["Luca"]
    assert club["coach"].get("/api/admin/import-channels").status_code == 403
    gone = admin.delete(f"/api/admin/import-channels/{added.json()['id']}")
    assert gone.status_code == 204
    assert admin.get("/api/admin/import-channels").json() == []


def link_video(client: TestClient, url: str = "https://youtu.be/NXNsZ_UNM6w") -> Any:
    return client.post(
        f"/api/teams/{H3}/videos", json={"game_id": game_id(H3, 1), "import_url": url}
    )


def allow_luca(admin: TestClient) -> None:
    r = admin.post(
        "/api/admin/import-channels", json={"channel": "@lucazbindenvolley2500", "label": "Luca"}
    )
    assert r.status_code == 201, r.text


def test_a_link_becomes_a_video_through_import_and_ingest(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore
) -> None:
    coach, admin = club["coach"], club["admin"]
    assert link_video(coach).status_code == 409  # no channel allowed yet
    allow_luca(admin)
    assert link_video(coach, "https://vimeo.com/1").status_code == 422
    both = coach.post(
        f"/api/teams/{H3}/videos",
        json={
            "game_id": game_id(H3, 1),
            "import_url": "https://youtu.be/NXNsZ_UNM6w",
            "files": [{"name": "a.mp4", "size": 10}],
        },
    )
    assert both.status_code == 422
    created = link_video(coach)
    assert created.status_code == 201, created.text
    video = created.json()
    assert (video["status"], video["source"]) == ("importing", "import")

    w = worker_client(app, admin, IMPORT_CAPS)
    assert claim(w, MEDIA_CAPS) == []  # import jobs need the import capability
    [job] = claim(w, IMPORT_CAPS)
    assert job["kind"] == "import"
    params = job["params"]
    assert params["url"] == "https://www.youtube.com/watch?v=NXNsZ_UNM6w"
    assert params["channels"] == ["lucazbindenvolley2500"]
    assert params["file_name"] == "youtube-NXNsZ_UNM6w.mp4"
    result = {"file_name": "youtube-NXNsZ_UNM6w.mp4", "bytes": 3_114_635_952, "info": LUCA}
    done = w.post(f"/api/worker/jobs/{job['id']}/complete", json=result)
    assert done.status_code == 204, done.text

    [ingest] = claim(w, IMPORT_CAPS)
    assert ingest["kind"] == "ingest"
    [f] = ingest["params"]["files"]
    assert (f["name"], f["bytes"], f["object_key"]) == (
        "youtube-NXNsZ_UNM6w.mp4",
        3_114_635_952,
        None,
    )
    r = w.post(f"/api/worker/jobs/{ingest['id']}/complete", json=ingest_result(ingest))
    assert r.status_code == 204, r.text
    while jobs := claim(w, IMPORT_CAPS):
        body = transcode_result(jobs[0]) if jobs[0]["kind"] == "transcode" else THUMBS
        assert w.post(f"/api/worker/jobs/{jobs[0]['id']}/complete", json=body).status_code == 204
    got = coach.get(f"/api/videos/{video['id']}").json()
    assert got["status"] == "ready"
    assert got["imported_from"] == {
        "platform": "youtube",
        "url": "https://www.youtube.com/watch?v=NXNsZ_UNM6w",
        "channel": "Luca Zbinden Volley",
        "title": LUCA["title"],
    }


def test_a_channel_removed_meanwhile_refuses_the_import(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore
) -> None:
    coach, admin = club["coach"], club["admin"]
    allow_luca(admin)
    video = link_video(coach).json()
    w = worker_client(app, admin, IMPORT_CAPS)
    [job] = claim(w, IMPORT_CAPS)
    [channel] = admin.get("/api/admin/import-channels").json()
    assert admin.delete(f"/api/admin/import-channels/{channel['id']}").status_code == 204
    result = {"file_name": "youtube-NXNsZ_UNM6w.mp4", "bytes": 10, "info": LUCA}
    assert w.post(f"/api/worker/jobs/{job['id']}/complete", json=result).status_code == 204
    got = coach.get(f"/api/videos/{video['id']}").json()
    assert got["status"] == "refused"
    [purge] = claim(w, IMPORT_CAPS)  # the downloaded file is removed again
    assert purge["kind"] == "purge"
    assert purge["params"]["raw_subdir"] == job["params"]["raw_subdir"]


def test_a_foreign_result_is_rejected(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore
) -> None:
    allow_luca(club["admin"])
    link_video(club["coach"])
    w = worker_client(app, club["admin"], IMPORT_CAPS)
    [job] = claim(w, IMPORT_CAPS)
    wrong = {"file_name": "youtube-AAAAAAAAAAA.mp4", "bytes": 10, "info": LUCA}
    assert w.post(f"/api/worker/jobs/{job['id']}/complete", json=wrong).status_code == 422


def test_admins_add_the_import_capability_to_a_worker(
    app: FastAPI, club: dict[str, TestClient]
) -> None:
    admin = club["admin"]
    created = admin.post("/api/admin/workers", json={"name": "lenovo", "caps": MEDIA_CAPS}).json()
    wid = created["worker"]["id"]
    r = admin.put(f"/api/admin/workers/{wid}", json={"caps": [*MEDIA_CAPS, "import"]})
    assert r.status_code == 200, r.text
    assert "import" in r.json()["caps"]
    assert admin.put(f"/api/admin/workers/{wid}", json={"caps": ["teleport"]}).status_code == 422
    assert admin.put(f"/api/admin/workers/{wid}", json={"paused": True}).json()["paused"] is True


# --- The worker step, with yt-dlp replaced -------------------------------------------------


class FakeYdl:
    """Stands in for yt_dlp.YoutubeDL: answers with `info`, 'downloads' a small file."""

    def __init__(self, options: dict[str, Any], info: dict[str, Any]) -> None:
        self.options = options
        self.info = info
        self.downloaded = False

    def __enter__(self) -> FakeYdl:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def extract_info(self, url: str, download: bool) -> dict[str, Any]:
        assert not download and url == "https://www.youtube.com/watch?v=NXNsZ_UNM6w"
        assert self.options["allowed_extractors"] == importer.EXTRACTORS
        assert self.options["noplaylist"] is True
        return dict(self.info)

    def process_ie_result(self, info: dict[str, Any], download: bool) -> dict[str, Any]:
        path = Path(self.options["paths"]["home"]) / "download.mp4"
        path.write_bytes(b"\0" * 1000)
        for hook in self.options["progress_hooks"]:
            hook({"status": "downloading", "downloaded_bytes": 500, "total_bytes": 1000})
        self.downloaded = True
        return {**info, "requested_downloads": [{"filepath": str(path)}]}


def fetch_with(info: dict[str, Any], tmp_path: Path, **kw: Any) -> tuple[Path, dict[str, Any]]:
    seen: list[float] = []
    path, kept = importer.fetch(
        "https://www.youtube.com/watch?v=NXNsZ_UNM6w",
        "youtube",
        kw.get("channels", ["lucazbindenvolley2500"]),
        tmp_path,
        max_seconds=kw.get("max_seconds", 6 * 3600),
        max_bytes=kw.get("max_bytes", 10**9),
        progress=seen.append,
        cancel=threading.Event(),
    )
    assert seen == [0.5]
    return path, kept


def test_the_worker_checks_the_channel_before_downloading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    made: list[FakeYdl] = []

    def factory(info: dict[str, Any]) -> Any:
        def make(options: dict[str, Any]) -> FakeYdl:
            made.append(FakeYdl(options, info))
            return made[-1]

        return make

    monkeypatch.setattr(importer, "_downloader", factory({**LUCA, "duration": 7920}))
    path, kept = fetch_with(LUCA, tmp_path)
    assert path.read_bytes() == b"\0" * 1000
    assert kept["channel_id"] == LUCA["channel_id"] and kept["duration"] == 7920
    assert made[0].options["js_runtimes"] == {"node": {}}

    for info, message in [
        ({**LUCA, "uploader_id": "@other", "channel_id": "UC" + "x" * 22, "uploader_url": "",
          "channel_url": ""}, "not on the import allow-list"),
        ({**LUCA, "live_status": "is_live"}, "Live streams"),
        ({**LUCA, "duration": 7 * 3600}, "longer than 6 hours"),
    ]:  # fmt: skip
        made.clear()
        monkeypatch.setattr(importer, "_downloader", factory(info))
        with pytest.raises(importer.Refused, match=message):
            fetch_with(info, tmp_path)
        assert not made[0].downloaded  # nothing fetched
