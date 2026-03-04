#!/usr/bin/env python3
"""Regression test for IPC + file double logging + mid-run replan."""

from __future__ import annotations

import json
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from runtime.control_plane import count_control_messages, send_control_ipc, serve_control_ipc
from runtime.event_store import EventStore
from runtime.orchestrator import RuntimeOrchestrator


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


def main() -> int:
    project_root = Path(".").resolve()
    pe_root = project_root / ".plan-executor"
    run_id = f"ipc-replan-{utc_compact()}"
    runbook = pe_root / "runbooks" / f"{run_id}.json"
    manifest = pe_root / "team-manifests" / f"{run_id}.json"

    write_json(
        runbook,
        {
            "meta": {
                "generated_at_utc": utc_compact(),
                "mode": "sequential",
                "task_type": "code",
                "max_parallel_workers": 1,
            },
            "dag": {"nodes": [{"id": "lane-1", "depends_on": []}, {"id": "lane-2", "depends_on": ["lane-1"]}]},
            "lanes": [
                {
                    "id": "lane-1",
                    "owner_role": "planner",
                    "scope": "sleep lane",
                    "commands": [_python_inline_command("import time; time.sleep(1); print('lane1')")],
                },
                {
                    "id": "lane-2",
                    "owner_role": "frontend",
                    "scope": "will be replanned",
                    "commands": [_python_inline_command("import sys; sys.exit(1)")],
                },
            ],
            "checkpoints": [{"id": "checkpoint-1", "after_lanes": ["lane-1", "lane-2"], "gate_criteria": ["ok"], "gate_commands": []}],
            "limits": {
                "max_replan": 3,
                "ai_worker_skip_warn_streak": 2,
                "ai_worker_skip_fail_streak": 0,
            },
        },
    )
    write_json(
        manifest,
        {
            "meta": {"generated_at_utc": utc_compact(), "adapter": "process-worker"},
            "workers": [
                {
                    "id": "worker-1",
                    "role": "planner",
                    "command_template": "{cmd}",
                    "engine": "",
                    "timeout_sec": 180,
                    "max_retries": 1,
                    "backoff_sec": 1.5,
                },
                {
                    "id": "worker-2",
                    "role": "frontend",
                    "command_template": "{cmd}",
                    "engine": "",
                    "timeout_sec": 180,
                    "max_retries": 1,
                    "backoff_sec": 1.5,
                },
            ],
        },
    )

    host = "127.0.0.1"
    port = 8877
    stop_event = threading.Event()
    server_thread = threading.Thread(
        target=serve_control_ipc,
        kwargs={"project_root": project_root, "host": host, "port": port, "stop_event": stop_event},
        daemon=True,
    )
    server_thread.start()
    time.sleep(0.25)

    result: Dict[str, Any] = {}

    def run_orchestrator() -> None:
        orch = RuntimeOrchestrator(project_root)
        result["state"] = orch.start(runbook_path=runbook, manifest_path=manifest, adapter_name="process-worker", run_id=run_id)

    run_thread = threading.Thread(target=run_orchestrator, daemon=True)
    run_thread.start()

    # Wait until lane-1 likely running, then send replan to patch lane-2.
    time.sleep(0.35)
    resp = send_control_ipc(
        host=host,
        port=port,
        message={
            "run_id": run_id,
            "kind": "replan",
            "payload": {
                "reason": "mid-run consensus adjustment",
                "update_lanes": [
                    {
                        "id": "lane-2",
                        "owner_role": "frontend",
                        "scope": "replanned lane-2",
                        "commands": ["echo replanned-lane-2"],
                    }
                ],
            },
        },
    )
    run_thread.join(timeout=30)
    stop_event.set()
    server_thread.join(timeout=2)

    if "state" not in result:
        print("[FAIL] orchestrator did not finish")
        return 1
    state = result["state"]
    store = EventStore(project_root)
    events = store.read_events(run_id, limit=0)
    status = str(state.get("status", "unknown"))
    replan_applied = any(str(e.get("event", "")) == "replan_applied" for e in events)
    control_received = any(str(e.get("event", "")) == "control_message_received" for e in events)
    control_count = count_control_messages(project_root, run_id)

    print("IPC Control Regression Test")
    print("=" * 36)
    print(f"send_response={resp}")
    print(f"run_id={run_id} status={status}")
    print(f"replan_applied={replan_applied} control_message_received={control_received} control_count={control_count}")

    ok = status == "completed" and replan_applied and control_received and control_count >= 1
    print(f"RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
