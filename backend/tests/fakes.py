"""An in-memory object store with the same interface as S3Store."""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from wieplay.core.storage import UploadedPart


class NoSuchUpload(Exception):
    pass


@dataclass
class FakeStore:
    objects: dict[str, int] = field(default_factory=dict)  # key -> size
    uploads: dict[str, dict[str, object]] = field(default_factory=dict)
    signed: list[tuple[str, str, int]] = field(default_factory=list)  # (op, key, ttl)
    contents: dict[str, bytes] = field(default_factory=dict)  # objects put with put_object
    _ids: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    def presign_get(self, key: str, ttl: int, download_name: str | None = None) -> str:
        self.signed.append(("get", key, ttl))
        name = f"&download={download_name}" if download_name else ""
        return f"https://s3.test/{key}?X-Amz-Expires={ttl}&X-Amz-Signature=x{name}"

    def presign_put(self, key: str, ttl: int) -> str:
        self.signed.append(("put", key, ttl))
        return f"https://s3.test/{key}?X-Amz-Expires={ttl}&put=1"

    def create_multipart(self, key: str, content_type: str) -> str:
        upload_id = f"up{next(self._ids)}"
        self.uploads[upload_id] = {"key": key, "parts": {}}
        return upload_id

    def presign_part(self, key: str, upload_id: str, part: int, size: int, ttl: int) -> str:
        assert self.uploads[upload_id]["key"] == key
        return f"https://s3.test/{key}?uploadId={upload_id}&partNumber={part}&size={size}"

    def put_part(self, upload_id: str, part: int, size: int) -> None:
        """Test helper: what a browser PUT to a part URL does."""
        parts = self.uploads[upload_id]["parts"]
        assert isinstance(parts, dict)
        parts[part] = size

    def list_parts(self, key: str, upload_id: str) -> list[UploadedPart]:
        if upload_id not in self.uploads:
            raise NoSuchUpload()
        parts = self.uploads[upload_id]["parts"]
        assert isinstance(parts, dict)
        return [UploadedPart(n, f'"etag{n}"', size) for n, size in sorted(parts.items())]

    def complete_multipart(self, key: str, upload_id: str, parts: Sequence[UploadedPart]) -> None:
        if upload_id not in self.uploads:
            raise NoSuchUpload()
        self.objects[key] = sum(p.size for p in parts)
        del self.uploads[upload_id]

    def upload_gone(self, exc: Exception) -> bool:
        return isinstance(exc, NoSuchUpload)

    def abort_multipart(self, key: str, upload_id: str) -> None:
        self.uploads.pop(upload_id, None)

    def size(self, key: str) -> int | None:
        return self.objects.get(key)

    def put_object(self, key: str, data: bytes) -> None:
        """Test helper: what a worker's PUT to a signed URL does."""
        self.objects[key] = len(data)
        self.contents[key] = data

    def read(self, key: str, max_bytes: int) -> bytes | None:
        data = self.contents.get(key)
        return None if data is None else data[:max_bytes]

    def delete(self, keys: Iterable[str]) -> None:
        for key in keys:
            self.objects.pop(key, None)
            self.contents.pop(key, None)

    def delete_prefix(self, prefix: str) -> int:
        keys = [k for k in self.objects if k.startswith(prefix)]
        self.delete(keys)
        return len(keys)
