#!/usr/bin/env python3
"""CLI for delegate-worker runtime."""

from __future__ import annotations

import argparse
from pathlib import Path

from runtime.delegate_bus import queue_stats
from runtime.delegate_worker import process_one_request, serve


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="plan-executor delegate worker CLI")
    parser.add_argument("--project-root", default=".", help="Project root containing .plan-executor")
    parser.add_argument("--worker-id", default="delegate-1", help="Worker id")
    parser.add_argument("--role-filter", default="", help="Optional owner_role filter")
    parser.add_argument("--engine", default="shell", choices=["shell", "process", "codex", "gemini"], help="Execution engine")
    parser.add_argument("--command-template", default="", help="Optional command template containing {cmd}")
    parser.add_argument("--timeout-sec", type=int, default=180)

    sub = parser.add_subparsers(dest="command", required=True)

    once = sub.add_parser("run-once", help="Process a single request")
    once.add_argument("--repeat", type=int, default=1)

    srv = sub.add_parser("serve", help="Serve delegate queue loop")
    srv.add_argument("--interval-sec", type=float, default=0.5)
    srv.add_argument("--max-jobs", type=int, default=0)
    srv.add_argument("--idle-exit-sec", type=float, default=0.0)

    sub.add_parser("stats", help="Show delegate queue stats")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    try:
        if args.command == "stats":
            stats = queue_stats(project_root)
            print(
                f"pending={stats['pending']} processing={stats['processing']} "
                f"done={stats['done']} responses={stats['responses']}"
            )
            return 0

        if args.command == "run-once":
            repeat = max(1, args.repeat)
            processed = 0
            passed = 0
            failed = 0
            for _ in range(repeat):
                out = process_one_request(
                    project_root=project_root,
                    worker_id=args.worker_id,
                    role_filter=args.role_filter,
                    engine=args.engine,
                    command_template=args.command_template,
                    timeout_sec=max(10, args.timeout_sec),
                )
                if out.get("processed", 0) <= 0:
                    continue
                processed += 1
                if out.get("status") == "pass":
                    passed += 1
                elif out.get("status") == "fail":
                    failed += 1
            print(f"processed={processed} passed={passed} failed={failed}")
            return 0

        if args.command == "serve":
            out = serve(
                project_root=project_root,
                worker_id=args.worker_id,
                role_filter=args.role_filter,
                engine=args.engine,
                command_template=args.command_template,
                timeout_sec=max(10, args.timeout_sec),
                interval_sec=max(0.05, args.interval_sec),
                max_jobs=max(0, args.max_jobs),
                idle_exit_sec=max(0.0, args.idle_exit_sec),
            )
            print(
                f"processed={out['processed']} passed={out['passed']} failed={out['failed']} "
                f"pending={out['queue']['pending']} processing={out['queue']['processing']} "
                f"done={out['queue']['done']} responses={out['queue']['responses']}"
            )
            return 0

        print("[ERROR] unsupported command")
        return 1
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

