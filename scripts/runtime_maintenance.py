#!/usr/bin/env python3
"""Non-core maintenance utilities for plan-executor runtime artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Iterable


def pe_root(project_root: Path) -> Path:
    return project_root.resolve() / ".plan-executor"


def safe_unlink(path: Path) -> bool:
    if path.exists() and path.is_file():
        path.unlink()
        return True
    return False


def safe_rmtree(path: Path) -> int:
    if not path.exists() or not path.is_dir():
        return 0
    count = 0
    for p in sorted(path.rglob("*"), reverse=True):
        try:
            if p.is_file():
                p.unlink()
                count += 1
            elif p.is_dir():
                p.rmdir()
        except OSError:
            continue
    try:
        path.rmdir()
    except OSError:
        pass
    return count


def prune_runs(project_root: Path, keep: int) -> Dict[str, int]:
    root = pe_root(project_root)
    state_dir = root / "state"
    events_dir = root / "events"
    logs_dir = root / "logs"
    state_dir.mkdir(parents=True, exist_ok=True)
    events_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    states = sorted(state_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    to_delete = states[max(0, keep):]
    removed_state = 0
    removed_events = 0
    removed_logs = 0

    for state_path in to_delete:
        run_id = state_path.stem
        if safe_unlink(state_path):
            removed_state += 1
        if safe_unlink(events_dir / f"{run_id}.jsonl"):
            removed_events += 1
        for lp in logs_dir.glob(f"{run_id}*"):
            if safe_unlink(lp):
                removed_logs += 1

    return {
        "removed_state": removed_state,
        "removed_events": removed_events,
        "removed_logs": removed_logs,
    }


def compact_events(project_root: Path, run_id: str, max_events: int) -> Dict[str, int]:
    root = pe_root(project_root)
    events_dir = root / "events"
    events_dir.mkdir(parents=True, exist_ok=True)

    targets: Iterable[Path]
    if run_id:
        targets = [events_dir / f"{run_id}.jsonl"]
    else:
        targets = list(events_dir.glob("*.jsonl"))

    compacted = 0
    removed_lines = 0
    for path in targets:
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) <= max_events:
            continue
        removed_lines += len(lines) - max_events
        path.write_text("\n".join(lines[-max_events:]) + "\n", encoding="utf-8")
        compacted += 1
    return {"compacted_files": compacted, "removed_lines": removed_lines}


def cleanup_worktrees(project_root: Path, run_id: str) -> Dict[str, int]:
    root = pe_root(project_root)
    wt_root = root / "worktrees"
    wt_root.mkdir(parents=True, exist_ok=True)
    removed_dirs = 0
    removed_files = 0

    targets: Iterable[Path]
    if run_id:
        targets = [wt_root / run_id]
    else:
        targets = [p for p in wt_root.iterdir() if p.is_dir()]

    for t in targets:
        removed_files += safe_rmtree(t)
        if not t.exists():
            removed_dirs += 1

    return {"removed_dirs": removed_dirs, "removed_files": removed_files}


def clear_queue(project_root: Path, bucket: str) -> Dict[str, int]:
    root = pe_root(project_root)
    queue_root = root / "queue"
    buckets = ["pending", "processing", "done", "failed"]
    removed = 0
    targets = buckets if bucket == "all" else [bucket]
    for b in targets:
        d = queue_root / b
        d.mkdir(parents=True, exist_ok=True)
        for p in d.glob("*.json"):
            if safe_unlink(p):
                removed += 1
    return {"removed_jobs": removed}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Maintenance utilities for .plan-executor artifacts.")
    parser.add_argument("--project-root", default=".", help="Project root containing .plan-executor")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prune-runs", help="Keep only newest N run state/event artifacts")
    p.add_argument("--keep", type=int, default=30)

    c = sub.add_parser("compact-events", help="Trim events jsonl to last N lines")
    c.add_argument("--run-id", default="")
    c.add_argument("--max-events", type=int, default=200)

    w = sub.add_parser("cleanup-worktrees", help="Remove runtime worktree directories")
    w.add_argument("--run-id", default="")

    q = sub.add_parser("clear-queue", help="Clear queue bucket jobs")
    q.add_argument("--bucket", choices=["pending", "processing", "done", "failed", "all"], default="pending")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root)
    if args.command == "prune-runs":
        out = prune_runs(project_root, keep=max(1, args.keep))
        print(f"[OK] removed_state={out['removed_state']} removed_events={out['removed_events']} removed_logs={out['removed_logs']}")
        return 0
    if args.command == "compact-events":
        out = compact_events(project_root, run_id=args.run_id, max_events=max(1, args.max_events))
        print(f"[OK] compacted_files={out['compacted_files']} removed_lines={out['removed_lines']}")
        return 0
    if args.command == "cleanup-worktrees":
        out = cleanup_worktrees(project_root, run_id=args.run_id)
        print(f"[OK] removed_dirs={out['removed_dirs']} removed_files={out['removed_files']}")
        return 0
    if args.command == "clear-queue":
        out = clear_queue(project_root, bucket=args.bucket)
        print(f"[OK] removed_jobs={out['removed_jobs']}")
        return 0
    print("[ERROR] unknown command")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
