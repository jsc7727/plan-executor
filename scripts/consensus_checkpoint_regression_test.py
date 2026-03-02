#!/usr/bin/env python3
"""Regression tests for checkpoint consensus-gate integration."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

from runtime.consensus_engine import create_round, submit_proposal, vote
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


def make_accepted_round(project_root: Path, run_id: str) -> str:
    rnd = create_round(
        project_root=project_root,
        run_id=run_id,
        topic="checkpoint-policy",
        participants=["planner", "qa"],
        threshold=0.6,
        quorum_ratio=0.5,
    )
    prop = submit_proposal(project_root, run_id, rnd.round_id, "planner", "option-a")
    vote(project_root, run_id, rnd.round_id, prop["proposal_id"], "planner", "approve", confidence=1.0, role="planner")
    vote(project_root, run_id, rnd.round_id, prop["proposal_id"], "qa", "approve", confidence=1.0, role="qa")
    return rnd.round_id


def run_case(project_root: Path, run_id: str, required_decision: str, expect_status: str) -> Tuple[bool, str]:
    pe_root = project_root / ".plan-executor"
    runbook = pe_root / "runbooks" / f"{run_id}.json"
    manifest = pe_root / "team-manifests" / f"{run_id}.json"
    round_id = make_accepted_round(project_root, run_id)
    build_manifest(manifest)
    write_json(
        runbook,
        {
            "meta": {"generated_at_utc": utc_compact(), "mode": "sequential", "task_type": "code", "max_parallel_workers": 1},
            "dag": {"nodes": [{"id": "lane-1", "depends_on": []}]},
            "lanes": [{"id": "lane-1", "owner_role": "planner", "scope": "single lane", "commands": ["echo checkpoint-consensus"]}],
            "checkpoints": [
                {
                    "id": "checkpoint-1",
                    "after_lanes": ["lane-1"],
                    "gate_criteria": ["consensus-required"],
                    "gate_commands": [],
                    "consensus_gate": {
                        "round_id": round_id,
                        "required_decision": required_decision,
                        "finalize": True,
                    },
                }
            ],
            "limits": {"max_replan": 3},
        },
    )

    orch = RuntimeOrchestrator(project_root)
    state = orch.start(runbook_path=runbook, manifest_path=manifest, adapter_name="process-worker", run_id=run_id)
    status = str(state.get("status", "unknown"))
    store = EventStore(project_root)
    events = store.read_events(run_id, limit=0)
    checkpoints = [e for e in events if str(e.get("event", "")) == "checkpoint"]
    last_cp = checkpoints[-1] if checkpoints else {}
    evidence = [str(x) for x in last_cp.get("payload", {}).get("evidence", [])]

    ok = status == expect_status
    if expect_status == "completed":
        ok = ok and any(x == "consensus-gate-pass" for x in evidence)
    else:
        ok = ok and any(x == "consensus-gate-failed" for x in evidence)
    return ok, f"status={status} expected={expect_status} evidence={evidence}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run consensus checkpoint gate regression scenarios.")
    parser.add_argument("--project-root", default=".", help="Project root containing .plan-executor")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    print("Consensus Checkpoint Regression Test")
    print("=" * 40)
    passed = 0

    ok1, detail1 = run_case(project_root, f"consensus-cp-pass-{utc_compact()}", required_decision="accepted", expect_status="completed")
    print(f"[{'PASS' if ok1 else 'FAIL'}] checkpoint-consensus-pass {detail1}")
    if ok1:
        passed += 1

    ok2, detail2 = run_case(project_root, f"consensus-cp-fail-{utc_compact()}", required_decision="rejected", expect_status="failed")
    print(f"[{'PASS' if ok2 else 'FAIL'}] checkpoint-consensus-fail {detail2}")
    if ok2:
        passed += 1

    print("-" * 40)
    print(f"RESULT: {passed}/2 passed")
    return 0 if passed == 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())

