#!/usr/bin/env python3
"""CLI for plan-executor independent runtime orchestration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime.orchestrator import RuntimeOrchestrator
from runtime.runbook_lint import lint_runbook_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="plan-executor runtime CLI")
    parser.add_argument(
        "--project-root",
        default=".",
        help="Project root containing .plan-executor runtime artifacts.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Start a new orchestration run.")
    start.add_argument("--runbook", required=True, help="Path to runbook JSON.")
    start.add_argument("--manifest", default="", help="Path to team manifest JSON.")
    start.add_argument("--adapter", default="auto", help="Worker adapter override.")
    start.add_argument("--run-id", default="", help="Optional explicit run id.")
    start.add_argument(
        "--skip-runbook-lint",
        action="store_true",
        help="Skip strict runbook lint before start.",
    )

    status = sub.add_parser("status", help="Show run status.")
    status.add_argument("--run-id", required=True)
    status.add_argument("--events", type=int, default=20, help="Number of recent events to show.")
    status.add_argument("--json", action="store_true", help="Print full JSON.")

    resume = sub.add_parser("resume", help="Resume failed/incomplete run.")
    resume.add_argument("--run-id", required=True)

    abort = sub.add_parser("abort", help="Abort a running run.")
    abort.add_argument("--run-id", required=True)
    abort.add_argument("--reason", default="user-request")

    sub.add_parser("runs", help="List known runs.")
    return parser.parse_args()


def print_state_summary(state: dict) -> None:
    print(f"run_id={state.get('run_id')} status={state.get('status')} adapter={state.get('adapter')}")
    print(f"current_lane_index={state.get('current_lane_index')} lanes={len(state.get('lanes', []))}")
    for lane in state.get("lanes", []):
        print(
            f"  - {lane.get('id')}: status={lane.get('status')} "
            f"attempts={lane.get('attempts')} owner={lane.get('owner_role')}"
        )


def main() -> int:
    args = parse_args()
    orchestrator = RuntimeOrchestrator(Path(args.project_root))
    try:
        if args.command == "start":
            runbook = Path(args.runbook).resolve()
            manifest = Path(args.manifest).resolve() if args.manifest else None
            if not bool(args.skip_runbook_lint):
                lint = lint_runbook_file(runbook, strict=True)
                if not bool(lint.get("ok", False)):
                    print("[ERROR] runbook lint failed")
                    for row in lint.get("errors", []):
                        print(
                            f"  - {row.get('code', 'lint-error')} "
                            f"path={row.get('path', '')} message={row.get('message', '')}"
                        )
                    return 2
                for row in lint.get("warnings", []):
                    print(
                        f"[WARN] runbook lint: {row.get('code', 'lint-warning')} "
                        f"path={row.get('path', '')} message={row.get('message', '')}"
                    )
            state = orchestrator.start(
                runbook_path=runbook,
                manifest_path=manifest,
                adapter_name=args.adapter,
                run_id=args.run_id or None,
            )
            print("[OK] run started")
            print_state_summary(state)
            return 0

        if args.command == "status":
            out = orchestrator.status(args.run_id, limit=args.events)
            if args.json:
                print(json.dumps(out, indent=2))
                return 0
            print_state_summary(out["state"])
            print(f"recent_events={len(out['events'])}")
            if out["events"]:
                tail = out["events"][-5:]
                for evt in tail:
                    print(f"  * {evt.get('ts')} {evt.get('event')}")
            return 0

        if args.command == "resume":
            state = orchestrator.resume(args.run_id)
            print("[OK] resume attempted")
            print_state_summary(state)
            return 0

        if args.command == "abort":
            state = orchestrator.abort(args.run_id, args.reason)
            print("[OK] abort applied")
            print_state_summary(state)
            return 0

        if args.command == "runs":
            runs = orchestrator.list_runs()
            if not runs:
                print("no runs")
                return 0
            for run_id, status in runs:
                print(f"{run_id}\t{status}")
            return 0

        raise AssertionError("unreachable command")
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
