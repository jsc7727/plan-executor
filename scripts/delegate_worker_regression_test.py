#!/usr/bin/env python3
"""E2E regression test for delegate-worker adapter."""

from __future__ import annotations

import argparse
import json
import platform
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from runtime.delegate_bus import queue_stats
from runtime.delegate_worker import serve
from runtime.event_store import EventStore
from runtime.orchestrator import RuntimeOrchestrator


def utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _shell_command_template() -> str:
    is_windows = platform.system().strip().lower().startswith("win")
    return "cmd /c {cmd}" if is_windows else "{cmd}"


def build_runbook(path: Path) -> None:
    payload = {
        "meta": {
            "generated_at_utc": utc_compact(),
            "preset": "product-web-app",
            "profile": "speed",
            "mode": "parallel",
            "task_type": "code",
            "max_parallel_workers": 2,
        },
        "dag": {
            "nodes": [
                {"id": "lane-1", "depends_on": []},
                {"id": "lane-2", "depends_on": []},
            ]
        },
        "lanes": [
            {
                "id": "lane-1",
                "owner_role": "planner",
                "commands": ["echo delegate-lane-1"],
            },
            {
                "id": "lane-2",
                "owner_role": "designer",
                "commands": ["echo delegate-lane-2"],
            },
        ],
        "checkpoints": [
            {
                "id": "checkpoint-1",
                "after_lanes": ["lane-1", "lane-2"],
                "gate_criteria": ["delegate-lanes-pass"],
                "gate_commands": [],
            }
        ],
        "limits": {
            "max_replan": 3,
            "stall_rounds_threshold": 2,
        },
    }
    write_json(path, payload)


def build_manifest(path: Path) -> None:
    cmd_template = _shell_command_template()
    payload = {
        "meta": {
            "generated_at_utc": utc_compact(),
            "mode": "teams-pipeline",
            "adapter": "delegate-worker",
            "requested_adapter": "delegate-worker",
            "task_type": "code",
            "worker_count": 2,
        },
        "workers": [
            {
                "id": "delegate-1",
                "role": "planner",
                "engine": "shell",
                "command_template": cmd_template,
                "timeout_sec": 30,
                "poll_sec": 0.1,
            },
            {
                "id": "delegate-2",
                "role": "designer",
                "engine": "shell",
                "command_template": cmd_template,
                "timeout_sec": 30,
                "poll_sec": 0.1,
            },
        ],
    }
    write_json(path, payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run delegate-worker E2E regression scenario.")
    parser.add_argument("--project-root", default=".", help="Project root containing .plan-executor")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    pe_root = project_root / ".plan-executor"
    runbook = pe_root / "runbooks" / "delegate-regression-runbook.json"
    manifest = pe_root / "team-manifests" / "delegate-regression-manifest.json"
    run_id = f"delegate-e2e-{utc_compact()}"
    build_runbook(runbook)
    build_manifest(manifest)

    worker_results: list[dict[str, Any]] = []

    def _serve(worker_id: str, role: str) -> None:
        out = serve(
            project_root=project_root,
            worker_id=worker_id,
            role_filter=role,
            engine="shell",
            command_template=_shell_command_template(),
            timeout_sec=30,
            interval_sec=0.05,
            max_jobs=1,
            idle_exit_sec=10.0,
        )
        worker_results.append({"worker_id": worker_id, "role": role, "out": out})

    threads = [
        threading.Thread(target=_serve, args=("delegate-1", "planner"), daemon=True),
        threading.Thread(target=_serve, args=("delegate-2", "designer"), daemon=True),
    ]
    for t in threads:
        t.start()

    time.sleep(0.15)
    orch = RuntimeOrchestrator(project_root)
    state = orch.start(
        runbook_path=runbook,
        manifest_path=manifest,
        adapter_name="delegate-worker",
        run_id=run_id,
    )
    for t in threads:
        t.join(timeout=12)

    store = EventStore(project_root)
    events = store.read_events(run_id, limit=0)
    lane_done = [e for e in events if str(e.get("event")) == "lane_done"]
    lane_pass = [e for e in lane_done if str(e.get("payload", {}).get("status")) == "pass"]
    q = queue_stats(project_root)

    ok = (
        str(state.get("status", "")) == "completed"
        and len(lane_done) >= 2
        and len(lane_pass) >= 2
        and q.get("responses", 0) >= 2
    )

    print("Delegate Worker Regression Test")
    print("=" * 40)
    print(
        f"run_id={run_id} status={state.get('status')} "
        f"lane_done={len(lane_done)} lane_pass={len(lane_pass)} responses={q.get('responses', 0)}"
    )
    print(f"worker_results={worker_results}")
    print(f"RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
