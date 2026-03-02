#!/usr/bin/env python3
"""Regression tests for synthetic-vote template based consensus autocreate."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

from runtime.event_store import EventStore
from runtime.orchestrator import RuntimeOrchestrator


def utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_manifest(path: Path) -> None:
    write_json(
        path,
        {
            "meta": {"generated_at_utc": utc_compact(), "adapter": "process-worker"},
            "workers": [
                {"id": "worker-1", "role": "planner", "command_template": "{cmd}", "timeout_sec": 60, "max_retries": 1, "backoff_sec": 1.0},
            ],
        },
    )


def build_template(path: Path, template_id: str, decision: str) -> None:
    write_json(
        path,
        {
            "id": template_id,
            "votes": [
                {"author": "planner", "role": "planner", "decision": decision, "confidence": 1.0, "weight": 1.0},
                {"author": "qa", "role": "qa", "decision": decision, "confidence": 1.0, "weight": 1.0},
            ],
        },
    )


def run_case(project_root: Path, case_id: str, template_name: str, required_decision: str, expect_status: str) -> Tuple[bool, str]:
    pe_root = project_root / ".plan-executor"
    run_id = f"{case_id}-{utc_compact()}"
    runbook = pe_root / "runbooks" / f"{run_id}.json"
    manifest = pe_root / "team-manifests" / f"{run_id}.json"
    build_manifest(manifest)
    write_json(
        runbook,
        {
            "meta": {"generated_at_utc": utc_compact(), "mode": "sequential", "task_type": "code", "max_parallel_workers": 1},
            "dag": {"nodes": [{"id": "lane-1", "depends_on": []}]},
            "lanes": [{"id": "lane-1", "owner_role": "planner", "scope": "single lane", "commands": ["echo consensus-template"]}],
            "checkpoints": [
                {
                    "id": "checkpoint-1",
                    "after_lanes": ["lane-1"],
                    "gate_criteria": ["consensus-template"],
                    "gate_commands": [],
                    "consensus_gate": {
                        "auto_create_round": True,
                        "participants": ["planner", "qa"],
                        "synthetic_votes_template": template_name,
                        "required_decision": required_decision,
                        "finalize": True,
                    },
                }
            ],
        },
    )
    orch = RuntimeOrchestrator(project_root)
    state = orch.start(runbook_path=runbook, manifest_path=manifest, adapter_name="process-worker", run_id=run_id)
    status = str(state.get("status", "unknown"))
    store = EventStore(project_root)
    events = store.read_events(run_id, limit=0)
    checkpoints = [e for e in events if str(e.get("event", "")) == "checkpoint"]
    last_cp = checkpoints[-1] if checkpoints else {}
    evidence = [str(x).strip().lower() for x in last_cp.get("payload", {}).get("evidence", [])]
    template_hit = any(x.startswith("consensus-synthetic-template:") for x in evidence)

    ok = status == expect_status and template_hit
    if expect_status == "completed":
        ok = ok and "consensus-gate-pass" in evidence
    else:
        ok = ok and "consensus-gate-failed" in evidence
    return ok, f"run_id={run_id} status={status} evidence={evidence}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run consensus synthetic-vote template regression scenarios.")
    parser.add_argument("--project-root", default=".", help="Project root containing .plan-executor")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    template_dir = project_root / ".plan-executor" / "consensus" / "templates"
    template_dir.mkdir(parents=True, exist_ok=True)
    build_template(template_dir / "template-pass.json", "template-pass", "approve")
    build_template(template_dir / "template-fail.json", "template-fail", "reject")

    print("Consensus Template Regression Test")
    print("=" * 40)
    passed = 0
    ok1, d1 = run_case(project_root, "consensus-template-pass", "template-pass", "accepted", "completed")
    print(f"[{'PASS' if ok1 else 'FAIL'}] template-pass {d1}")
    if ok1:
        passed += 1
    ok2, d2 = run_case(project_root, "consensus-template-fail", "template-fail", "accepted", "failed")
    print(f"[{'PASS' if ok2 else 'FAIL'}] template-fail {d2}")
    if ok2:
        passed += 1

    print("-" * 40)
    print(f"RESULT: {passed}/2 passed")
    return 0 if passed == 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())

