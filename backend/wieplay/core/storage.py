"""Object storage over the S3 API (PLAN §7.5, §7.6).

Signing is local HMAC (no network call). Only the API holds the credentials: browsers get
signed part URLs for their uploads and signed GET URLs per media segment; workers ask the API
to sign the keys their job may touch (PLAN §8.1).

Key layout in the bucket:
    incoming/<video>/<part_no>/<file name>    browser upload staging (lifecycle: 14 days)
    media/<video>/<rendition>/init.mp4, seg_00001.m4s …   HLS renditions
    media/<video>/thumbs/sprite_001.jpg …, poster.jpg     scrub previews
    media/<video>/analysis/ball-track-<token>.json.gz       the ball's path (PLAN §10.4)
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import boto3
from botocore.config import Config

from wieplay.core.config import Settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

CONTENT_TYPES = {
    ".m4s": "video/iso.segment",
    ".mp4": "video/mp4",
    ".jpg": "image/jpeg",
    ".m3u8": "application/vnd.apple.mpegurl",
    ".gz": "application/gzip",
}


@dataclass(frozen=True)
class UploadedPart:
    number: int
    etag: str
    size: int


class ObjectStore(Protocol):
    def presign_get(self, key: str, ttl: int, download_name: str | None = None) -> str: ...
    def presign_put(self, key: str, ttl: int) -> str: ...
    def create_multipart(self, key: str, content_type: str) -> str: ...
    def presign_part(self, key: str, upload_id: str, part: int, size: int, ttl: int) -> str: ...
    def list_parts(self, key: str, upload_id: str) -> list[UploadedPart]: ...
    def complete_multipart(
        self, key: str, upload_id: str, parts: Sequence[UploadedPart]
    ) -> None: ...
    def abort_multipart(self, key: str, upload_id: str) -> None: ...
    def upload_gone(self, exc: Exception) -> bool: ...
    def size(self, key: str) -> int | None: ...
    def read(self, key: str, max_bytes: int) -> bytes | None: ...
    def delete(self, keys: Iterable[str]) -> None: ...
    def delete_prefix(self, prefix: str) -> int: ...


def content_type_for(key: str) -> str:
    for suffix, kind in CONTENT_TYPES.items():
        if key.endswith(suffix):
            return kind
    return "application/octet-stream"


SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_file_name(name: str) -> str:
    """A camera file name, reduced to characters that are safe in an object key."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = SAFE_NAME.sub("_", base).strip("._") or "video"
    return cleaned[-120:]


class S3Store:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        self._client: S3Client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if settings.s3_path_style else "virtual"},
                retries={"max_attempts": 4, "mode": "standard"},
                connect_timeout=10,
                read_timeout=60,
            ),
        )

    def presign_get(self, key: str, ttl: int, download_name: str | None = None) -> str:
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if download_name:
            params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
        return self._client.generate_presigned_url("get_object", Params=params, ExpiresIn=ttl)

    def presign_put(self, key: str, ttl: int) -> str:
        return self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type_for(key)},
            ExpiresIn=ttl,
        )

    def create_multipart(self, key: str, content_type: str) -> str:
        response = self._client.create_multipart_upload(
            Bucket=self.bucket, Key=key, ContentType=content_type
        )
        return response["UploadId"]

    def presign_part(self, key: str, upload_id: str, part: int, size: int, ttl: int) -> str:
        """Signs the exact Content-Length: a part URL takes that many bytes and no more."""
        return self._client.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "UploadId": upload_id,
                "PartNumber": part,
                "ContentLength": size,
            },
            ExpiresIn=ttl,
        )

    def list_parts(self, key: str, upload_id: str) -> list[UploadedPart]:
        parts: list[UploadedPart] = []
        marker = 0
        while True:
            response = self._client.list_parts(
                Bucket=self.bucket, Key=key, UploadId=upload_id, PartNumberMarker=marker
            )
            parts += [
                UploadedPart(p["PartNumber"], p["ETag"], p["Size"])
                for p in response.get("Parts", [])
            ]
            if not response.get("IsTruncated"):
                return parts
            marker = response["NextPartNumberMarker"]

    def complete_multipart(self, key: str, upload_id: str, parts: Sequence[UploadedPart]) -> None:
        self._client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": [{"PartNumber": p.number, "ETag": p.etag} for p in parts]},
        )

    def abort_multipart(self, key: str, upload_id: str) -> None:
        self._client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)

    def upload_gone(self, exc: Exception) -> bool:
        """True for NoSuchUpload: the multipart upload was completed or aborted already."""
        response: dict[str, Any] = getattr(exc, "response", None) or {}
        return bool(response.get("Error", {}).get("Code") == "NoSuchUpload")

    def size(self, key: str) -> int | None:
        try:
            return self._client.head_object(Bucket=self.bucket, Key=key)["ContentLength"]
        except self._client.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise

    def read(self, key: str, max_bytes: int) -> bytes | None:
        """A small object's bytes (the ball track), None when it is not there; at most
        max_bytes are read."""
        try:
            response = self._client.get_object(
                Bucket=self.bucket, Key=key, Range=f"bytes=0-{max_bytes - 1}"
            )
        except self._client.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        data: bytes = response["Body"].read()
        return data

    def delete(self, keys: Iterable[str]) -> None:
        batch: list[Any] = []
        for key in keys:
            batch.append({"Key": key})
            if len(batch) == 1000:
                self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": batch})
                batch = []
        if batch:
            self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": batch})

    def delete_prefix(self, prefix: str) -> int:
        paginator = self._client.get_paginator("list_objects_v2")
        keys = [
            obj["Key"]
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix)
            for obj in page.get("Contents", [])
        ]
        self.delete(keys)
        return len(keys)

    def apply_bucket_rules(self, allowed_origins: Sequence[str]) -> None:
        """CORS for browser uploads and playback; lifecycle rules for staging (PLAN §5.5)."""
        self._client.put_bucket_cors(
            Bucket=self.bucket,
            CORSConfiguration={
                "CORSRules": [
                    {
                        "AllowedOrigins": list(allowed_origins),
                        "AllowedMethods": ["GET", "PUT", "HEAD"],
                        "AllowedHeaders": ["*"],
                        "ExposeHeaders": ["ETag"],
                        "MaxAgeSeconds": 3600,
                    }
                ]
            },
        )
        self._client.put_bucket_lifecycle_configuration(
            Bucket=self.bucket,
            LifecycleConfiguration={
                "Rules": [
                    {
                        "ID": "incoming-14d",
                        "Status": "Enabled",
                        "Filter": {"Prefix": "incoming/"},
                        "Expiration": {"Days": 14},
                        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 14},
                    },
                    {
                        "ID": "exports-30d",
                        "Status": "Enabled",
                        "Filter": {"Prefix": "exports/"},
                        "Expiration": {"Days": 30},
                    },
                    {
                        "ID": "analysis-3d",
                        "Status": "Enabled",
                        "Filter": {"Prefix": "analysis/"},
                        "Expiration": {"Days": 3},
                    },
                ]
            },
        )


def make_store(settings: Settings) -> ObjectStore | None:
    return S3Store(settings) if settings.video_enabled else None
