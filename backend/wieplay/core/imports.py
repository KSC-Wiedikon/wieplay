"""Videos imported from YouTube or Twitch links (PLAN §7.2.2).

Only two kinds of address are ever fetched, both parsed strictly here: a YouTube video
(`youtube.com/watch?v=…`, `youtu.be/…`, `youtube.com/live/…`) and a Twitch video
(`twitch.tv/videos/…`). Anything else is refused before a job exists, so the worker never
requests an address a member typed (no SSRF). The video's channel must be on the
allow-list (`import_channel`); the worker checks it before downloading and the API checks
it again when the import finishes.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from wieplay.core.models import ImportChannel

Platform = Literal["youtube", "twitch"]

YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")
TWITCH_ID = re.compile(r"[0-9]{5,15}")
YOUTUBE_CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}")
YOUTUBE_HANDLE = re.compile(r"[A-Za-z0-9._-]{3,30}")
TWITCH_LOGIN = re.compile(r"[A-Za-z0-9_]{3,25}")

YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com"})
TWITCH_HOSTS = frozenset({"twitch.tv", "www.twitch.tv", "m.twitch.tv"})

# Longest import accepted (a tournament day stream), and the biggest file.
MAX_SECONDS = 6 * 3600
MAX_BYTES = 30 * 1024**3


class NotImportable(ValueError):
    """A link or channel that cannot be imported; the message is shown to the user."""


@dataclass(frozen=True)
class ImportSource:
    platform: Platform
    external_id: str

    @property
    def url(self) -> str:
        """The canonical address the worker fetches (never the text the user typed)."""
        if self.platform == "youtube":
            return f"https://www.youtube.com/watch?v={self.external_id}"
        return f"https://www.twitch.tv/videos/{self.external_id}"

    @property
    def file_name(self) -> str:
        return f"{self.platform}-{self.external_id}.mp4"


def _split(raw: str, limit: int) -> tuple[str, str, str] | None:
    """A plain web address → (host, path, query); None for anything else (other schemes,
    user info, ports, spaces, or text the URL parser cannot read)."""
    if len(raw) > limit or any(c.isspace() for c in raw):
        return None
    try:
        parts = urlsplit(raw if "://" in raw else "https://" + raw)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or parts.username or parts.password or port:
        return None
    return (parts.hostname or "").lower(), parts.path.rstrip("/"), parts.query


def parse_link(text: str) -> ImportSource:
    """A YouTube or Twitch video link → its platform and id. Raises NotImportable."""
    split = _split(text.strip(), 500)
    if split is None:
        raise NotImportable("This is not a YouTube or Twitch video link")
    host, path, query = split
    if host in YOUTUBE_HOSTS:
        if path == "/watch":
            ids = parse_qs(query).get("v", [])
            if len(ids) == 1 and YOUTUBE_ID.fullmatch(ids[0]):
                return ImportSource("youtube", ids[0])
        match = re.fullmatch(r"/(?:live|shorts)/([^/]+)", path)
        if match and YOUTUBE_ID.fullmatch(match.group(1)):
            return ImportSource("youtube", match.group(1))
    elif host == "youtu.be":
        candidate = path.lstrip("/")
        if YOUTUBE_ID.fullmatch(candidate):
            return ImportSource("youtube", candidate)
    elif host in TWITCH_HOSTS:
        match = re.fullmatch(r"/videos/([^/]+)", path)
        if match and TWITCH_ID.fullmatch(match.group(1)):
            return ImportSource("twitch", match.group(1))
    raise NotImportable(
        "Only YouTube videos (youtube.com/watch?v=…, youtu.be/…) and Twitch videos "
        "(twitch.tv/videos/…) can be imported"
    )


def parse_channel(text: str) -> tuple[Platform, str]:
    """What an admin types for the allow-list → (platform, ref). Accepts a YouTube channel
    link or id (UC…), a YouTube handle (@name or its link), or a Twitch channel link."""
    raw = text.strip()
    if raw.startswith("@") and YOUTUBE_HANDLE.fullmatch(raw[1:]):
        return "youtube", raw[1:].lower()
    if YOUTUBE_CHANNEL_ID.fullmatch(raw):
        return "youtube", raw
    split = _split(raw, 300)
    if split is not None:
        host, path, _ = split
        if host in YOUTUBE_HOSTS:
            match = re.fullmatch(r"/@([^/]+)(?:/.*)?", path)
            if match and YOUTUBE_HANDLE.fullmatch(match.group(1)):
                return "youtube", match.group(1).lower()
            match = re.fullmatch(r"/channel/([^/]+)(?:/.*)?", path)
            if match and YOUTUBE_CHANNEL_ID.fullmatch(match.group(1)):
                return "youtube", match.group(1)
        if host in TWITCH_HOSTS:
            match = re.fullmatch(r"/([^/]+)", path)
            if match and TWITCH_LOGIN.fullmatch(match.group(1)) and match.group(1) != "videos":
                return "twitch", match.group(1).lower()
    raise NotImportable(
        "Give a YouTube channel (link, @handle or UC… id) or a Twitch channel link (twitch.tv/name)"
    )


def info_refs(platform: str, info: Mapping[str, Any]) -> set[str]:
    """The ways a video's metadata (yt-dlp's info dict) names its channel, normalised like
    `parse_channel`: channel id, handle, login."""
    refs: set[str] = set()
    if platform == "youtube":
        channel_id = info.get("channel_id")
        if isinstance(channel_id, str) and YOUTUBE_CHANNEL_ID.fullmatch(channel_id):
            refs.add(channel_id)
        for key in ("uploader_id", "channel_handle"):
            value = info.get(key)
            if isinstance(value, str) and value.startswith("@"):
                refs.add(value[1:].lower())
        url = info.get("uploader_url") or info.get("channel_url")
        if isinstance(url, str):
            with contextlib.suppress(NotImportable):
                refs.add(parse_channel(url)[1])
    elif platform == "twitch":
        for key in ("uploader_id", "channel_id", "uploader"):
            value = info.get(key)
            if isinstance(value, str) and TWITCH_LOGIN.fullmatch(value):
                refs.add(value.lower())
    return refs


def allowed_refs(db: Session, platform: str) -> set[str]:
    return set(db.scalars(select(ImportChannel.ref).where(ImportChannel.platform == platform)))


def channel_allowed(platform: str, info: Mapping[str, Any], allowed: Iterable[str]) -> bool:
    return bool(info_refs(platform, info) & set(allowed))
