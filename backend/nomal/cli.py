"""Operations commands.

python -m nomal.cli storage-setup              bucket CORS + lifecycle rules (PLAN §5.5)
python -m nomal.cli create-worker NAME CAPS    register a worker, print its token once
python -m nomal.cli add-worker-cap NAME CAP    give an existing worker one more capability
python -m nomal.cli allow-channel CHANNEL LABEL  allow imports from a YouTube/Twitch channel
python -m nomal.cli import-batch FILE --by NAME  queue many imports, paced (FILE or - for stdin)
"""

from __future__ import annotations

import argparse
import sys
import uuid
from typing import Any

from sqlalchemy import select

from nomal.core import jobs
from nomal.core.config import get_settings
from nomal.core.db import make_engine, make_sessionmaker
from nomal.core.models import AuditLog, Worker
from nomal.core.storage import S3Store


def storage_setup() -> int:
    settings = get_settings()
    if not settings.video_enabled:
        print("NOMAL_S3_ENDPOINT / NOMAL_S3_BUCKET are not set", file=sys.stderr)
        return 1
    S3Store(settings).apply_bucket_rules([settings.public_origin])
    print(f"bucket {settings.s3_bucket}: CORS for {settings.public_origin}, lifecycle rules set")
    return 0


def create_worker(name: str, caps: str) -> int:
    settings = get_settings()
    sessions = make_sessionmaker(make_engine(settings.database_url))
    with sessions() as db:
        if db.scalar(select(Worker.id).where(Worker.name == name)):
            print(f"worker {name} exists; revoke it in Admin first", file=sys.stderr)
            return 1
        token, token_hash = jobs.new_worker_token()
        worker = Worker(
            id=uuid.uuid4(),
            name=name,
            caps=sorted({c.strip() for c in caps.split(",") if c.strip()}),
            token_hash=token_hash,
        )
        db.add(worker)
        db.add(
            AuditLog(
                action="worker_created",
                object_type="worker",
                object_id=str(worker.id),
                details={"name": name, "caps": worker.caps, "via": "cli"},
            )
        )
        db.commit()
    print(token)
    return 0


def add_worker_cap(name: str, cap: str) -> int:
    from nomal.api.admin import KNOWN_CAPS

    if cap not in KNOWN_CAPS:
        print(f"unknown capability {cap}", file=sys.stderr)
        return 1
    settings = get_settings()
    sessions = make_sessionmaker(make_engine(settings.database_url))
    with sessions() as db:
        worker = db.scalar(select(Worker).where(Worker.name == name))
        if worker is None:
            print(f"no worker {name}", file=sys.stderr)
            return 1
        if cap not in worker.caps:
            before = sorted(worker.caps)
            worker.caps = sorted({*worker.caps, cap})
            db.add(
                AuditLog(
                    action="worker_caps_changed",
                    object_type="worker",
                    object_id=str(worker.id),
                    details={"before": before, "after": worker.caps, "via": "cli"},
                )
            )
            db.commit()
        print(",".join(worker.caps))
    return 0


def allow_channel(channel: str, label: str) -> int:
    from nomal.core import imports
    from nomal.core.models import ImportChannel

    try:
        platform, ref = imports.parse_channel(channel)
    except imports.NotImportable as exc:
        print(exc, file=sys.stderr)
        return 1
    settings = get_settings()
    sessions = make_sessionmaker(make_engine(settings.database_url))
    with sessions() as db:
        found = db.scalar(
            select(ImportChannel).where(
                ImportChannel.platform == platform, ImportChannel.ref == ref
            )
        )
        if found is None:
            row = ImportChannel(id=uuid.uuid4(), platform=platform, ref=ref, label=label)
            db.add(row)
            db.add(
                AuditLog(
                    action="import_channel_added",
                    object_type="import_channel",
                    object_id=str(row.id),
                    details={"platform": platform, "ref": ref, "via": "cli"},
                )
            )
            db.commit()
        print(f"{platform}:{ref}")
    return 0


def import_batch(path: str, by: str, spacing_min: int, dry_run: bool) -> int:
    from datetime import timedelta

    from nomal.core import batch
    from nomal.core.filming import CLUB_TZ
    from nomal.core.models import WdMember
    from nomal.core.sync import HttpBridgeClient

    if path == "-":
        text = sys.stdin.read()
    else:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    try:
        lines = batch.parse(text)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    settings = get_settings()
    if settings.dev_bridge:
        from nomal.api.dev_bridge import FixtureBridgeClient

        bridge: Any = FixtureBridgeClient()
    else:
        bridge = HttpBridgeClient(settings.bridge_api_url, settings.bridge_service_token)
    sessions = make_sessionmaker(make_engine(settings.database_url))
    with sessions() as db:
        members = list(db.scalars(select(WdMember).where(WdMember.display_name == by)))
        if len(members) != 1:
            print(f"--by must name exactly one member, found {len(members)}", file=sys.stderr)
            return 2
        planned = batch.plan(db, bridge, lines)
        for p in planned:
            if p.problem:
                print(f"line {p.line.number}: skipped: {p.problem}")
        if dry_run:
            ok = [p for p in planned if not p.problem]
            for p in ok:
                print(f"line {p.line.number}: would import {p.title} ({p.line.on:%d.%m.%Y})")
            db.rollback()
            return 0
        queued = batch.queue(
            db, planned, created_by=members[0].id, spacing=timedelta(minutes=spacing_min)
        )
        for p, video, start in queued:
            print(
                f"line {p.line.number}: queued {p.title} ({p.line.on:%d.%m.%Y}), "
                f"starts {start.astimezone(CLUB_TZ):%H:%M}, video {video.id}"
            )
        print(f"{len(queued)} queued, {len(planned) - len(queued)} skipped")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nomal")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("storage-setup")
    worker = sub.add_parser("create-worker")
    worker.add_argument("name")
    worker.add_argument("caps", help="comma-separated, e.g. ingest,transcode,thumbs,nas,inbox")
    cap = sub.add_parser("add-worker-cap")
    cap.add_argument("name")
    cap.add_argument("cap", help="e.g. import")
    channel = sub.add_parser("allow-channel")
    channel.add_argument("channel", help="YouTube link, @handle or UC… id, or a Twitch link")
    channel.add_argument("label")
    batch_cmd = sub.add_parser("import-batch")
    batch_cmd.add_argument("file", help="tab-separated: team id, date, link [, title]; - = stdin")
    batch_cmd.add_argument("--by", required=True, help="the member it is done for (full name)")
    batch_cmd.add_argument("--spacing-min", type=int, default=10)
    batch_cmd.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "import-batch":
        return import_batch(args.file, args.by, args.spacing_min, args.dry_run)
    if args.command == "storage-setup":
        return storage_setup()
    if args.command == "add-worker-cap":
        return add_worker_cap(args.name, args.cap)
    if args.command == "allow-channel":
        return allow_channel(args.channel, args.label)
    return create_worker(args.name, args.caps)


if __name__ == "__main__":
    raise SystemExit(main())
