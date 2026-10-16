"""Operations commands.

python -m nomal.cli storage-setup              bucket CORS + lifecycle rules (PLAN §5.5)
python -m nomal.cli create-worker NAME CAPS    register a worker, print its token once
python -m nomal.cli add-worker-cap NAME CAP    give an existing worker one more capability
python -m nomal.cli allow-channel CHANNEL LABEL  allow imports from a YouTube/Twitch channel
python -m nomal.cli import-batch FILE --by NAME  queue many imports, paced (FILE or - for stdin)
python -m nomal.cli analyse --video ID | --team TEAM_ID | --all-ready   queue the analysis
python -m nomal.cli train-rallies              train the rally finder now
python -m nomal.cli rally-eval [--run ID]      per-game scores of v0 and v1 (latest training)
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


def analyse(
    video: str | None, team: int | None, all_ready: bool, force: bool, dry_run: bool
) -> int:
    """Queue the analysis of ready volleyball videos (the backfill of imported games).
    Videos analysed before are skipped unless --force; one with an analysis running is
    always skipped."""
    from nomal.core import analysis
    from nomal.core.models import AnalysisArtifact, Video

    settings = get_settings()
    sessions = make_sessionmaker(make_engine(settings.database_url))
    with sessions() as db:
        query = select(Video).where(Video.status == "ready").order_by(Video.recorded_on)
        if video:
            query = query.where(Video.id == uuid.UUID(video))
        elif team is not None:
            query = query.where(Video.team_id == team)
        elif not all_ready:
            print("name --video, --team or --all-ready", file=sys.stderr)
            return 2
        done = set(
            db.scalars(select(AnalysisArtifact.video_id).where(AnalysisArtifact.kind == "features"))
        )
        queued = 0
        for row in db.scalars(query):
            label = f"{row.recorded_on:%d.%m.%Y} {row.title} ({row.id})"
            reason = analysis.can_analyse(row)
            if reason:
                print(f"skip {label}: {reason}")
                continue
            if analysis.active_job(db, "analyse", row.id) is not None:
                print(f"skip {label}: an analysis is queued or running")
                continue
            if row.id in done and not force:
                print(f"skip {label}: analysed before (--force to run again)")
                continue
            if dry_run:
                print(f"would queue {label}")
                continue
            job = analysis.queue_analyse(db, row)
            db.add(
                AuditLog(
                    action="analysis_requested",
                    object_type="video",
                    object_id=str(row.id),
                    details={"job": str(job.id), "via": "cli"},
                )
            )
            queued += 1
            print(f"queued {label}: job {job.id}")
        if dry_run:
            db.rollback()
        else:
            db.commit()
        print(f"{queued} queued")
    return 0


def train_rallies() -> int:
    from datetime import timedelta

    from nomal.core import analysis

    settings = get_settings()
    sessions = make_sessionmaker(make_engine(settings.database_url))
    with sessions() as db:
        job = analysis.queue_training(db, timedelta(0))
        db.commit()
        print(f"training job {job.id} queued")
    return 0


def _pct(value: Any) -> str:
    return f"{100 * float(value):5.1f}" if isinstance(value, int | float) else "    -"


def rally_eval(run_id: str | None, as_json: bool) -> int:
    """Per-game boundary F1 (±1 s), rally F1 and counts of v0 and v1, each game scored while
    it was held out of the training (leave one game out), from the latest training run."""
    import json

    from nomal.core import analysis
    from nomal.core.models import ModelRun, Video

    settings = get_settings()
    sessions = make_sessionmaker(make_engine(settings.database_url))
    with sessions() as db:
        run = db.get(ModelRun, uuid.UUID(run_id)) if run_id else analysis.latest_training(db)
        if run is None or run.kind != "train_rallies":
            print("no training run yet (python -m nomal.cli train-rallies)", file=sys.stderr)
            return 1
        videos = run.metrics.get("videos", {})
        if as_json:
            print(json.dumps({"run": str(run.id), **run.metrics}, indent=2, default=str))
            return 0
        titles = {
            str(v.id): f"{v.recorded_on:%d.%m.%Y} {v.title}"[:40]
            for v in db.scalars(select(Video).where(Video.id.in_([uuid.UUID(k) for k in videos])))
        }
        print(
            f"training {run.id} ({run.created_at:%d.%m.%Y %H:%M}), finder in use: "
            f"{run.metrics.get('finder')}, v1 accepted: {run.metrics.get('accepted')}"
        )
        print(f"{'game':40}  {'rallies':>7}  {'v0 bF1':>6} {'v0 n':>5}  {'v1 bF1':>6} {'v1 n':>5}")
        for key, entry in videos.items():
            lovo = entry.get("lovo")
            if not lovo:
                continue
            v0, v1 = lovo.get("v0", {}), lovo.get("v1") or {}
            print(
                f"{titles.get(key, key):40}  {v0.get('rallies_true', 0):7d}  "
                f"{_pct(v0.get('boundary', {}).get('f1'))}% {v0.get('rallies_pred', 0):5d}  "
                f"{_pct(v1.get('boundary', {}).get('f1'))}% {v1.get('rallies_pred', 0):5d}"
            )
        summary = run.metrics.get("summary", {})
        for finder in ("v0", "v1"):
            s = summary.get(finder) or {}
            if s.get("videos"):
                print(
                    f"{finder}: {s['videos']} games, boundary F1 {_pct(s.get('boundary_f1'))}% "
                    f"(pooled {_pct(s.get('boundary_f1_pooled'))}%), rally F1 "
                    f"{_pct(s.get('rally_f1'))}%, live IoU {_pct(s.get('live_iou'))}%"
                )
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
    analyse_cmd = sub.add_parser("analyse", help="queue the automatic analysis")
    which = analyse_cmd.add_mutually_exclusive_group(required=True)
    which.add_argument("--video", help="one video id")
    which.add_argument("--team", type=int, help="every ready video of a Wiedisync team row")
    which.add_argument("--all-ready", action="store_true", help="every ready volleyball video")
    analyse_cmd.add_argument("--force", action="store_true", help="also videos analysed before")
    analyse_cmd.add_argument("--dry-run", action="store_true")
    sub.add_parser("train-rallies", help="train the rally finder now")
    evaluation = sub.add_parser("rally-eval", help="per-game scores of the rally finders")
    evaluation.add_argument("--run", help="a training run id (default: the latest)")
    evaluation.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "analyse":
        return analyse(args.video, args.team, args.all_ready, args.force, args.dry_run)
    if args.command == "train-rallies":
        return train_rallies()
    if args.command == "rally-eval":
        return rally_eval(args.run, args.json)
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
