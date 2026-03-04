#!/usr/bin/env python3
"""Regression tests for plan-search candidate scoring and selection."""

from __future__ import annotations

import json
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from runtime.control_plane import send_control_ipc, serve_control_ipc
from runtime.event_store import EventStore
from runtime.orchestrator import RuntimeOrchestrator
from runtime.plan_search import score_replan_candidate, select_best_replan_candidate


def utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _resolve_python_cmd() -> str:
    for candidate in ["python", "python3"]:
        if shutil.which(candidate):
            return candidate
    exe = str(sys.executable).strip()
    return exe or "python3"


def _python_inline_command(script: str) -> str:
    py = _resolve_python_cmd()
    py_token = f'"{py}"' if " " in py else py
    escaped = script.replace("\\", "\\\\").replace('"', '\\"')
    return f'{py_token} -c "{escaped}"'


def unit_candidate_selection() -> tuple[bool, str]:
    baseline = ["lane-1", "lane-2"]
    bad = {
        "id": "bad-cycle",
        "confidence": 0.9,
        "risk_penalty": 8.0,
        "plan_patch": {
            "update_lanes": [{"id": "lane-2", "commands": ["echo bad"]}],
            "dag": {"nodes": [{"id": "lane-1", "depends_on": []}, {"id": "lane-2", "depends_on": ["lane-2"]}]},
        },
    }
    good = {
        "id": "good-fix",
        "confidence": 0.7,
        "plan_patch": {
            "update_lanes": [{"id": "lane-2", "commands": ["echo good"]}],
            "dag": {"nodes": [{"id": "lane-1", "depends_on": []}, {"id": "lane-2", "depends_on": ["lane-1"]}]},
            "checkpoints": [{"id": "cp-1", "after_lanes": ["lane-1", "lane-2"], "gate_criteria": ["ok"], "gate_commands": []}],
        },
    }
    score_bad = score_replan_candidate(bad, baseline)
    score_good = score_replan_candidate(good, baseline)
    selected = select_best_replan_candidate([bad, good], baseline)
    ok = score_good["score"] > score_bad["score"] and selected.get("selected_id") == "good-fix"
    return ok, f"bad={score_bad['score']} good={score_good['score']} selected={selected.get('selected_id')}"


def e2e_candidate_replan(project_root: Path) -> tuple[bool, str]:
    pe_root = project_root / ".plan-executor"
    run_id = f"plan-search-e2e-{utc_compact()}"
    runbook = pe_root / "runbooks" / f"{run_id}.json"
    manifest = pe_root / "team-manifests" / f"{run_id}.json"

    write_json(
        runbook,
        {
            "meta": {"generated_at_utc": utc_compact(), "mode": "sequential", "task_type": "code", "max_parallel_workers": 1},
            "dag": {"nodes": [{"id": "lane-1", "depends_on": []}, {"id": "lane-2", "depends_on": ["lane-1"]}]},
            "lanes": [
                {
                    "id": "lane-1",
                    "owner_role": "planner",
                    "scope": "delay lane",
                    "commands": [_python_inline_command("import time; time.sleep(1); print('lane1')")],
                },
                {
                    "id": "lane-2",
                    "owner_role": "frontend",
                    "scope": "must replan",
                    "commands": [_python_inline_command("import sys; sys.exit(1)")],
                },
            ],
            "checkpoints": [{"id": "checkpoint-1", "after_lanes": ["lane-1", "lane-2"], "gate_criteria": ["ok"], "gate_commands": []}],
            "limits": {"max_replan": 3},
        },
    )
    write_json(
        manifest,
        {
            "meta": {"generated_at_utc": utc_compact(), "adapter": "process-worker"},
            "workers": [
                {"id": "worker-1", "role": "planner", "command_template": "{cmd}", "timeout_sec": 60, "max_retries": 1, "backoff_sec": 1.0},
                {"id": "worker-2", "role": "frontend", "command_template": "{cmd}", "timeout_sec": 60, "max_retries": 1, "backoff_sec": 1.0},
            ],
        },
    )

    host = "127.0.0.1"
    port = 8878
    stop_event = threading.Event()
    server_thread = threading.Thread(
        target=serve_control_ipc,
        kwargs={"project_root": project_root, "host": host, "port": port, "stop_event": stop_event},
        daemon=True,
    )
    server_thread.start()
    time.sleep(0.2)

    result: Dict[str, Any] = {}

    def run_orchestrator() -> None:
        orch = RuntimeOrchestrator(project_root)
        result["state"] = orch.start(runbook_path=runbook, manifest_path=manifest, adapter_name="process-worker", run_id=run_id)

    run_thread = threading.Thread(target=run_orchestrator, daemon=True)
    run_thread.start()
    time.sleep(0.35)

    send_control_ipc(
        host=host,
        port=port,
        message={
            "run_id": run_id,
            "kind": "replan",
            "payload": {
                "reason": "candidate-search",
                "candidate_plans": [
                    {
                        "id": "bad-cycle",
                        "confidence": 0.9,
                        "risk_penalty": 10.0,
                        "plan_patch": {
                            "update_lanes": [{"id": "lane-2", "commands": ["echo bad"]}],
                            "dag": {"nodes": [{"id": "lane-1", "depends_on": []}, {"id": "lane-2", "depends_on": ["lane-2"]}]},
                        },
                    },
                    {
                        "id": "good-fix",
                        "confidence": 0.7,
                        "plan_patch": {
                            "update_lanes": [{"id": "lane-2", "commands": ["echo plan-search-pass"]}],
                            "dag": {"nodes": [{"id": "lane-1", "depends_on": []}, {"id": "lane-2", "depends_on": ["lane-1"]}]},
                            "checkpoints": [
                                {
                                    "id": "checkpoint-1",
                                    "after_lanes": ["lane-1", "lane-2"],
                                    "gate_criteria": ["ok"],
                                    "gate_commands": [],
                                }
                            ],
                        },
                    },
                ],
            },
        },
    )

    run_thread.join(timeout=30)
    stop_event.set()
    server_thread.join(timeout=2)

    if "state" not in result:
        return False, "orchestrator-timeout"
    state = result["state"]
    store = EventStore(project_root)
    events = store.read_events(run_id, limit=0)
    selected = [e for e in events if str(e.get("event", "")) == "replan_candidate_selected"]
    status = str(state.get("status", "unknown"))
    selected_id = ""
    if selected:
        selected_id = str(selected[-1].get("payload", {}).get("selected_id", ""))

    ok = status == "completed" and selected_id == "good-fix"
    return ok, f"status={status} selected_id={selected_id}"


def main() -> int:
    project_root = Path(".").resolve()
    print("Plan Search Regression Test")
    print("=" * 40)
    passed = 0

    ok1, detail1 = unit_candidate_selection()
    print(f"[{'PASS' if ok1 else 'FAIL'}] unit-candidate-selection {detail1}")
    if ok1:
        passed += 1

    ok2, detail2 = e2e_candidate_replan(project_root)
    print(f"[{'PASS' if ok2 else 'FAIL'}] e2e-candidate-replan {detail2}")
    if ok2:
        passed += 1

    print("-" * 40)
    print(f"RESULT: {passed}/2 passed")
    return 0 if passed == 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
