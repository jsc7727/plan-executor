#!/usr/bin/env python3
"""CLI for plan-executor queue daemon."""

from __future__ import annotations

import argparse
from pathlib import Path

from runtime.daemon import RuntimeDaemon


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="plan-executor daemon CLI")
    parser.add_argument("--project-root", default=".", help="Project root containing .plan-executor")
    sub = parser.add_subparsers(dest="command", required=True)

    enqueue = sub.add_parser("enqueue", help="Enqueue a runbook job")
    enqueue.add_argument("--runbook", required=True)
    enqueue.add_argument("--manifest", default="")
    enqueue.add_argument("--adapter", default="auto")
    enqueue.add_argument("--run-id", default="")
    enqueue.add_argument(
        "--skip-runbook-lint",
        action="store_true",
        help="Skip strict runbook lint before queueing job.",
    )

    once = sub.add_parser("run-once", help="Process queued jobs once")
    once.add_argument("--max-jobs", type=int, default=1)

    serve = sub.add_parser("serve", help="Run daemon loop")
    serve.add_argument("--interval", type=float, default=1.0)
    serve.add_argument("--max-jobs-per-tick", type=int, default=1)

    recover = sub.add_parser("recover", help="Recover stale processing jobs")
    recover.add_argument("--stale-sec", type=int, default=120)
    recover.add_argument("--max-retries", type=int, default=2)

    sub.add_parser("stats", help="Show queue stats")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    daemon = RuntimeDaemon(Path(args.project_root))
    try:
        if args.command == "enqueue":
            runbook = Path(args.runbook).resolve()
            manifest = Path(args.manifest).resolve() if args.manifest else None
            path = daemon.enqueue(
                runbook=runbook,
                manifest=manifest,
                adapter=args.adapter,
                run_id=args.run_id or None,
                skip_runbook_lint=bool(args.skip_runbook_lint),
            )
            print(f"[OK] enqueued: {path}")
            return 0

        if args.command == "run-once":
            out = daemon.run_once(max_jobs=max(1, args.max_jobs))
            print(
                f"[OK] processed={out['processed']} "
                f"succeeded={out['succeeded']} failed={out['failed']} "
                f"recovered={out['recovered']} recovery_failed={out['recovery_failed']}"
            )
            return 0

        if args.command == "serve":
            print(
                f"[OK] daemon start interval={args.interval}s "
                f"max_jobs_per_tick={args.max_jobs_per_tick}"
            )
            daemon.serve(interval_sec=max(0.2, args.interval), max_jobs_per_tick=max(1, args.max_jobs_per_tick))
            return 0

        if args.command == "recover":
            out = daemon.recover_stale_processing(
                stale_sec=max(1, args.stale_sec),
                max_retries=max(0, args.max_retries),
            )
            print(f"[OK] recovered={out['recovered']} failed={out['failed']}")
            return 0

        if args.command == "stats":
            stats = daemon.queue_stats()
            print(
                f"pending={stats['pending']} processing={stats['processing']} "
                f"done={stats['done']} failed={stats['failed']}"
            )
            return 0

        raise AssertionError("unreachable command")
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
