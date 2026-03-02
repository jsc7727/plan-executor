#!/usr/bin/env python3
"""Generate team manifest for OMC-like orchestration modes."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import List


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap a team manifest for plan-executor.")
    parser.add_argument(
        "--mode",
        choices=["teams-pipeline", "swarm-style", "ultrapilot-style"],
        default="teams-pipeline",
        help="Compatibility mode.",
    )
    parser.add_argument(
        "--adapter",
        choices=["auto", "inline-worker", "worktree-worker", "tmux-worker", "process-worker", "ai-worker", "delegate-worker"],
        default="auto",
        help="Worker runtime adapter.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=3,
        help="Requested worker count (used in swarm/ultrapilot modes).",
    )
    parser.add_argument(
        "--task-type",
        choices=["code", "document", "research"],
        default="code",
        help="Primary task type.",
    )
    parser.add_argument(
        "--project-root",
        default=".",
        help="Project root where .plan-executor artifacts are stored (default: current directory).",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output JSON path. Defaults to <project-root>/.plan-executor/team-manifests/team-manifest-<timestamp>.json",
    )
    parser.add_argument(
        "--worker-cmd-template",
        default="",
        help="Optional command template for process-worker/ai-worker. Supports {cmd},{lane_id},{owner_role},{run_id},{worker_id},{worker_role}.",
    )
    parser.add_argument(
        "--ai-engine",
        choices=["mixed", "codex", "gemini"],
        default="codex",
        help="Engine assignment strategy for ai-worker.",
    )
    parser.add_argument(
        "--ai-timeout-sec",
        type=int,
        default=180,
        help="Per-command timeout for ai-worker command execution.",
    )
    parser.add_argument(
        "--ai-max-retries",
        type=int,
        default=1,
        help="Retry count for ai-worker command execution.",
    )
    parser.add_argument(
        "--ai-backoff-sec",
        type=float,
        default=1.5,
        help="Backoff base seconds between ai-worker retries.",
    )
    parser.add_argument(
        "--delegate-timeout-sec",
        type=int,
        default=180,
        help="Per-request timeout for delegate-worker.",
    )
    parser.add_argument(
        "--delegate-poll-sec",
        type=float,
        default=0.3,
        help="Poll interval seconds for delegate-worker response waits.",
    )
    return parser.parse_args()


def resolve_adapter(requested: str, workers: int) -> tuple[str, List[str]]:
    notes: List[str] = []
    has_tmux = shutil.which("tmux") is not None
    has_git = shutil.which("git") is not None

    if requested == "auto":
        if workers >= 3 and has_tmux:
            return "tmux-worker", notes
        if workers >= 2 and has_git:
            return "worktree-worker", notes
        return "inline-worker", notes

    if requested == "tmux-worker" and not has_tmux:
        notes.append("tmux not found, fallback to inline-worker.")
        return "inline-worker", notes
    if requested == "worktree-worker" and not has_git:
        notes.append("git not found, fallback to inline-worker.")
        return "inline-worker", notes
    if requested == "process-worker":
        return "process-worker", notes
    if requested == "ai-worker":
        return "ai-worker", notes
    if requested == "delegate-worker":
        return "delegate-worker", notes
    return requested, notes


def build_workers(mode: str, n: int) -> List[dict]:
    n = max(1, min(n, 8))
    if mode == "teams-pipeline":
        stages = ["plan", "design", "build", "verify", "release"]
        workers = []
        for i, stage in enumerate(stages, start=1):
            workers.append(
                {
                    "id": f"worker-{i}",
                    "role": f"{stage}-owner",
                    "stage": stage,
                    "contract": f"{stage}-artifact",
                    "engine": "",
                    "command_template": "",
                    "timeout_sec": 180,
                    "max_retries": 1,
                    "backoff_sec": 1.5,
                }
            )
        return workers

    if mode == "swarm-style":
        workers = []
        for i in range(1, n + 1):
            workers.append(
                {
                    "id": f"worker-{i}",
                    "role": "micro-worker",
                    "stage": "build",
                    "contract": f"micro-shard-{i}",
                    "engine": "",
                    "command_template": "",
                    "timeout_sec": 180,
                    "max_retries": 1,
                    "backoff_sec": 1.5,
                }
            )
        return workers

    # ultrapilot-style
    workers = [
        {
            "id": "worker-1",
            "role": "pilot",
            "stage": "plan",
            "contract": "global-decision-log",
            "engine": "",
            "command_template": "",
            "timeout_sec": 180,
            "max_retries": 1,
            "backoff_sec": 1.5,
        }
    ]
    specialist_roles = ["designer-specialist", "frontend-specialist", "backend-specialist", "qa-specialist"]
    for i in range(2, n + 1):
        role = specialist_roles[(i - 2) % len(specialist_roles)]
        workers.append(
            {
                "id": f"worker-{i}",
                "role": role,
                "stage": "build",
                "contract": f"{role}-artifact",
                "engine": "",
                "command_template": "",
                "timeout_sec": 180,
                "max_retries": 1,
                "backoff_sec": 1.5,
            }
        )
    return workers


def main() -> int:
    args = parse_args()
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    workers = max(1, args.workers)
    adapter, notes = resolve_adapter(args.adapter, workers)
    worker_specs = build_workers(args.mode, workers)
    if adapter == "ai-worker":
        engines: List[str] = []
        if args.ai_engine == "mixed":
            base = ["codex", "gemini"]
            for i in range(len(worker_specs)):
                engines.append(base[i % len(base)])
        else:
            engines = [args.ai_engine for _ in worker_specs]

        for w, engine in zip(worker_specs, engines):
            w["engine"] = engine
            w["timeout_sec"] = max(10, args.ai_timeout_sec)
            w["max_retries"] = max(0, args.ai_max_retries)
            w["backoff_sec"] = max(0.0, args.ai_backoff_sec)
            if args.worker_cmd_template.strip():
                base_template = args.worker_cmd_template.strip()
                w["command_template"] = (
                    base_template.replace("{worker_id}", str(w["id"])).replace("{worker_role}", str(w["role"]))
                )
            else:
                if engine == "gemini":
                    w["command_template"] = 'gemini -p "{cmd}" --yolo'
                else:
                    w["command_template"] = 'codex exec --skip-git-repo-check "{cmd}"'
    else:
        if args.worker_cmd_template.strip():
            base_template = args.worker_cmd_template.strip()
            for w in worker_specs:
                w["command_template"] = (
                    base_template.replace("{worker_id}", str(w["id"])).replace("{worker_role}", str(w["role"]))
                )
        elif adapter == "process-worker":
            for w in worker_specs:
                w["command_template"] = "{cmd}"
        elif adapter == "delegate-worker":
            for w in worker_specs:
                w["command_template"] = "{cmd}"
                w["timeout_sec"] = max(10, args.delegate_timeout_sec)
                w["poll_sec"] = max(0.05, args.delegate_poll_sec)

    project_root = Path(args.project_root).resolve()
    output_path = (
        Path(args.output).resolve()
        if args.output
        else project_root / ".plan-executor" / "team-manifests" / f"team-manifest-{now}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = {
        "meta": {
            "generated_at_utc": now,
            "mode": args.mode,
            "adapter": adapter,
            "requested_adapter": args.adapter,
            "task_type": args.task_type,
            "worker_count": len(worker_specs),
            "ai_engine": args.ai_engine if adapter == "ai-worker" else "",
        },
        "workers": worker_specs,
        "hooks": ["preflight", "lane_start", "lane_done", "checkpoint", "post_merge", "finalize"],
        "watchdog": {
            "heartbeat_required": True,
            "suspect_after_missed_cycles": 1,
            "recycle_after_missed_cycles": 2,
        },
        "notes": notes,
    }

    output_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if notes:
        for note in notes:
            print(f"[WARN] {note}")
    print(f"[OK] Team manifest written: {output_path}")
    print(f"[OK] mode={args.mode} adapter={adapter} workers={len(worker_specs)} task_type={args.task_type}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
