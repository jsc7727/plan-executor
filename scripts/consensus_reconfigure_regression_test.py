#!/usr/bin/env python3
"""Regression test for mid-run consensus reconfiguration via IPC control plane."""

from __future__ import annotations

import json
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from runtime.consensus_engine import create_round, submit_proposal, vote
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


def build_round(project_root: Path, run_id: str) -> tuple[str, str]:
    rnd = create_round(
        project_root=project_root,
        run_id=run_id,
        topic="mid-run-consensus-reconfigure",
        participants=["planner", "qa"],
        threshold=0.8,
        quorum_ratio=1.0,
        required_roles=["planner", "qa"],
    )
    prop = submit_proposal(
        project_root=project_root,
        run_id=run_id,
        round_id=rnd.round_id,
        author="planner",
        content="initial-proposal",
    )
    proposal_id = str(prop.get("proposal_id", "")).strip()
    vote(
        project_root=project_root,
        run_id=run_id,
        round_id=rnd.round_id,
        proposal_id=proposal_id,
        author="planner",
        decision="approve",
        confidence=1.0,
        role="planner",
    )
    return rnd.round_id, proposal_id


def main() -> int:
    project_root = Path(".").resolve()
    pe_root = project_root / ".plan-executor"
    run_id = f"consensus-reconfigure-{utc_compact()}"
    runbook = pe_root / "runbooks" / f"{run_id}.json"
    manifest = pe_root / "team-manifests" / f"{run_id}.json"
    round_id, proposal_id = build_round(project_root, run_id)

    write_json(
        runbook,
        {
            "meta": {
                "generated_at_utc": utc_compact(),
                "mode": "sequential",
                "task_type": "code",
                "max_parallel_workers": 1,
            },
            "dag": {"nodes": [{"id": "lane-1", "depends_on": []}]},
            "lanes": [
                {
                    "id": "lane-1",
                    "owner_role": "planner",
                    "scope": "sleep to allow mid-run control patch",
                    "commands": [_python_inline_command("import time; time.sleep(1.0); print('lane-1')")],
                }
            ],
            "checkpoints": [
                {
                    "id": "checkpoint-1",
                    "after_lanes": ["lane-1"],
                    "gate_criteria": ["consensus-required"],
                    "gate_commands": [],
                    "consensus_gate": {
                        "round_id": round_id,
                        "proposal_id": proposal_id,
                        "required_decision": "accepted",
                        "finalize": True,
                        "threshold": 0.8,
                        "quorum_ratio": 1.0,
                        "required_roles": ["planner", "qa"],
                    },
                }
            ],
            "limits": {
                "max_replan": 2,
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
                }
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
    time.sleep(0.25)

    result: Dict[str, Any] = {}

    def run_orchestrator() -> None:
        orch = RuntimeOrchestrator(project_root)
        result["state"] = orch.start(runbook_path=runbook, manifest_path=manifest, adapter_name="process-worker", run_id=run_id)

    run_thread = threading.Thread(target=run_orchestrator, daemon=True)
    run_thread.start()

    time.sleep(0.35)
    send_resp = send_control_ipc(
        host=host,
        port=port,
        message={
            "run_id": run_id,
            "kind": "consensus_reconfigure",
            "payload": {
                "reason": "mid-run-policy-relaxation",
                "checkpoint_id": "checkpoint-1",
                "consensus_gate_patch": {
                    "threshold": 0.5,
                    "quorum_ratio": 0.5,
                    "required_roles": [],
                },
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
    control_count = count_control_messages(project_root, run_id)
    reconfigured = [e for e in events if str(e.get("event", "")) == "consensus_reconfigured"]
    checkpoints = [e for e in events if str(e.get("event", "")) == "checkpoint"]
    last_cp = checkpoints[-1] if checkpoints else {}
    evidence = [str(x).strip().lower() for x in last_cp.get("payload", {}).get("evidence", [])]
    control_received = any(str(e.get("event", "")) == "control_message_received" for e in events)

    print("Consensus Reconfigure Regression Test")
    print("=" * 44)
    print(f"send_response={send_resp}")
    print(f"run_id={run_id} status={status}")
    print(f"control_count={control_count} control_received={control_received}")
    print(f"consensus_reconfigured_events={len(reconfigured)}")
    print(f"checkpoint_evidence={evidence}")

    ok = (
        status == "completed"
        and control_received
        and control_count >= 1
        and len(reconfigured) >= 1
        and "consensus-gate-pass" in evidence
    )
    print(f"RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
