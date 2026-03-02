#!/usr/bin/env python3
"""Regression tests for command guardrails (lane/gate enforcement)."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import platform
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
                {"id": "worker-1", "role": "planner", "command_template": "{cmd}", "timeout_sec": 60, "max_retries": 1, "backoff_sec": 1.0}
            ],
        },
    )


def run_lane_block_case(project_root: Path, run_id: str) -> Tuple[bool, str]:
    pe_root = project_root / ".plan-executor"
    runbook = pe_root / "runbooks" / f"{run_id}.json"
    manifest = pe_root / "team-manifests" / f"{run_id}.json"
    build_manifest(manifest)
    write_json(
        runbook,
        {
            "meta": {"generated_at_utc": utc_compact(), "mode": "sequential", "task_type": "code", "max_parallel_workers": 1},
            "dag": {"nodes": [{"id": "lane-1", "depends_on": []}]},
            "lanes": [{"id": "lane-1", "owner_role": "planner", "scope": "guardrail lane block", "commands": ["echo blocked-lane"]}],
            "checkpoints": [{"id": "checkpoint-1", "after_lanes": ["lane-1"], "gate_criteria": ["ok"], "gate_commands": []}],
            "limits": {
                "max_replan": 1,
                "command_guardrails": {
                    "enabled": True,
                    "mode": "enforce",
                    "denylist_patterns": ["^echo\\b"],
                    "phases": ["lane"],
                },
            },
        },
    )
    orch = RuntimeOrchestrator(project_root)
    state = orch.start(runbook_path=runbook, manifest_path=manifest, adapter_name="process-worker", run_id=run_id)
    status = str(state.get("status", "unknown"))
    store = EventStore(project_root)
    events = store.read_events(run_id, limit=0)
    lane_done = [e for e in events if str(e.get("event", "")) == "lane_done"]
    payload = lane_done[-1].get("payload", {}) if lane_done else {}
    evidence = [str(x).strip().lower() for x in payload.get("evidence", [])]
    err = str(payload.get("error", "")).strip().lower()
    ok = status == "failed" and "guardrail-blocked" in evidence and "guardrail" in err
    return ok, f"status={status} evidence={evidence} error={err[:120]}"


def run_gate_block_case(project_root: Path, run_id: str) -> Tuple[bool, str]:
    pe_root = project_root / ".plan-executor"
    runbook = pe_root / "runbooks" / f"{run_id}.json"
    manifest = pe_root / "team-manifests" / f"{run_id}.json"
    build_manifest(manifest)
    write_json(
        runbook,
        {
            "meta": {"generated_at_utc": utc_compact(), "mode": "sequential", "task_type": "code", "max_parallel_workers": 1},
            "dag": {"nodes": [{"id": "lane-1", "depends_on": []}]},
            "lanes": [{"id": "lane-1", "owner_role": "planner", "scope": "guardrail gate block", "commands": ['python -c "print(\'ok\')"']}],
            "checkpoints": [
                {
                    "id": "checkpoint-1",
                    "after_lanes": ["lane-1"],
                    "gate_criteria": ["guardrail"],
                    "gate_commands": ["echo gate-blocked"],
                }
            ],
            "limits": {
                "max_replan": 1,
                "command_guardrails": {
                    "enabled": True,
                    "mode": "enforce",
                    "allowlist_patterns": ["^python\\s+-c\\b"],
                    "phases": ["gate"],
                },
            },
        },
    )
    orch = RuntimeOrchestrator(project_root)
    state = orch.start(runbook_path=runbook, manifest_path=manifest, adapter_name="process-worker", run_id=run_id)
    status = str(state.get("status", "unknown"))
    store = EventStore(project_root)
    events = store.read_events(run_id, limit=0)
    checkpoints = [e for e in events if str(e.get("event", "")) == "checkpoint"]
    payload = checkpoints[-1].get("payload", {}) if checkpoints else {}
    evidence = [str(x).strip().lower() for x in payload.get("evidence", [])]
    err = str(payload.get("error", "")).strip().lower()
    ok = status == "failed" and "gate-command-guardrail-blocked" in evidence and "guardrail" in err
    return ok, f"status={status} evidence={evidence} error={err[:120]}"


def run_profile_template_case(project_root: Path, run_id: str) -> Tuple[bool, str]:
    pe_root = project_root / ".plan-executor"
    runbook = pe_root / "runbooks" / f"{run_id}.json"
    manifest = pe_root / "team-manifests" / f"{run_id}.json"
    build_manifest(manifest)
    write_json(
        runbook,
        {
            "meta": {"generated_at_utc": utc_compact(), "mode": "sequential", "task_type": "code", "max_parallel_workers": 1},
            "dag": {"nodes": [{"id": "lane-1", "depends_on": []}]},
            "lanes": [{"id": "lane-1", "owner_role": "planner", "scope": "guardrail profile block", "commands": ["git reset --hard"]}],
            "checkpoints": [{"id": "checkpoint-1", "after_lanes": ["lane-1"], "gate_criteria": ["ok"], "gate_commands": []}],
            "limits": {
                "max_replan": 1,
                "command_guardrails": {
                    "enabled": True,
                    "profile": "ci",
                    "os_template": "auto",
                    "phases": ["lane"],
                },
            },
        },
    )
    orch = RuntimeOrchestrator(project_root)
    state = orch.start(runbook_path=runbook, manifest_path=manifest, adapter_name="process-worker", run_id=run_id)
    status = str(state.get("status", "unknown"))
    store = EventStore(project_root)
    events = store.read_events(run_id, limit=0)
    lane_done = [e for e in events if str(e.get("event", "")) == "lane_done"]
    payload = lane_done[-1].get("payload", {}) if lane_done else {}
    evidence = [str(x).strip().lower() for x in payload.get("evidence", [])]
    err = str(payload.get("error", "")).strip().lower()
    ok = status == "failed" and "guardrail-blocked" in evidence and "guardrail" in err
    return ok, f"status={status} evidence={evidence} error={err[:120]}"


def run_human_approval_allow_case(project_root: Path, run_id: str) -> Tuple[bool, str]:
    pe_root = project_root / ".plan-executor"
    runbook = pe_root / "runbooks" / f"{run_id}.json"
    manifest = pe_root / "team-manifests" / f"{run_id}.json"
    build_manifest(manifest)
    write_json(
        runbook,
        {
            "meta": {"generated_at_utc": utc_compact(), "mode": "sequential", "task_type": "code", "max_parallel_workers": 1},
            "dag": {"nodes": [{"id": "lane-1", "depends_on": []}]},
            "lanes": [{"id": "lane-1", "owner_role": "planner", "scope": "human approval allow", "commands": ["echo approved"]}],
            "checkpoints": [{"id": "checkpoint-1", "after_lanes": ["lane-1"], "gate_criteria": ["ok"], "gate_commands": []}],
            "limits": {
                "max_replan": 1,
                "command_guardrails": {
                    "enabled": True,
                    "mode": "human-approval",
                    "denylist_patterns": ["^echo\\b"],
                    "approval_non_interactive_decision": "allow",
                    "phases": ["lane"],
                },
            },
        },
    )
    orch = RuntimeOrchestrator(project_root)
    state = orch.start(runbook_path=runbook, manifest_path=manifest, adapter_name="process-worker", run_id=run_id)
    status = str(state.get("status", "unknown"))
    store = EventStore(project_root)
    events = store.read_events(run_id, limit=0)
    lane_done = [e for e in events if str(e.get("event", "")) == "lane_done"]
    payload = lane_done[-1].get("payload", {}) if lane_done else {}
    evidence = [str(x).strip().lower() for x in payload.get("evidence", [])]
    ok = status == "completed" and any("guardrail-audit:human-approved" in x for x in evidence)
    return ok, f"status={status} evidence={evidence}"


def run_human_approval_deny_case(project_root: Path, run_id: str) -> Tuple[bool, str]:
    pe_root = project_root / ".plan-executor"
    runbook = pe_root / "runbooks" / f"{run_id}.json"
    manifest = pe_root / "team-manifests" / f"{run_id}.json"
    build_manifest(manifest)
    write_json(
        runbook,
        {
            "meta": {"generated_at_utc": utc_compact(), "mode": "sequential", "task_type": "code", "max_parallel_workers": 1},
            "dag": {"nodes": [{"id": "lane-1", "depends_on": []}]},
            "lanes": [{"id": "lane-1", "owner_role": "planner", "scope": "human approval deny", "commands": ["echo denied"]}],
            "checkpoints": [{"id": "checkpoint-1", "after_lanes": ["lane-1"], "gate_criteria": ["ok"], "gate_commands": []}],
            "limits": {
                "max_replan": 1,
                "command_guardrails": {
                    "enabled": True,
                    "mode": "human-approval",
                    "denylist_patterns": ["^echo\\b"],
                    "approval_non_interactive_decision": "deny",
                    "phases": ["lane"],
                },
            },
        },
    )
    orch = RuntimeOrchestrator(project_root)
    state = orch.start(runbook_path=runbook, manifest_path=manifest, adapter_name="process-worker", run_id=run_id)
    status = str(state.get("status", "unknown"))
    store = EventStore(project_root)
    events = store.read_events(run_id, limit=0)
    lane_done = [e for e in events if str(e.get("event", "")) == "lane_done"]
    payload = lane_done[-1].get("payload", {}) if lane_done else {}
    evidence = [str(x).strip().lower() for x in payload.get("evidence", [])]
    err = str(payload.get("error", "")).strip().lower()
    commands = payload.get("commands", [])
    guard_reason = ""
    if isinstance(commands, list) and commands:
        first = commands[0] if isinstance(commands[0], dict) else {}
        guard = first.get("guardrail", {}) if isinstance(first, dict) else {}
        if isinstance(guard, dict):
            guard_reason = str(guard.get("reason", "")).strip().lower()
    ok = status == "failed" and "guardrail-blocked" in evidence and guard_reason == "human-denied"
    return ok, f"status={status} evidence={evidence} guard_reason={guard_reason} error={err[:120]}"


def run_safe_path_auto_allow_case(project_root: Path, run_id: str) -> Tuple[bool, str]:
    pe_root = project_root / ".plan-executor"
    runbook = pe_root / "runbooks" / f"{run_id}.json"
    manifest = pe_root / "team-manifests" / f"{run_id}.json"
    build_manifest(manifest)
    is_windows = platform.system().strip().lower().startswith("win")
    setup_cmd = 'if not exist temp\\build-cache mkdir temp\\build-cache' if is_windows else "mkdir -p ./temp/build-cache"
    delete_cmd = "rmdir /s /q .\\temp\\build-cache" if is_windows else "rm -rf ./temp/build-cache"
    write_json(
        runbook,
        {
            "meta": {"generated_at_utc": utc_compact(), "mode": "sequential", "task_type": "code", "max_parallel_workers": 1, "environment": "dev"},
            "dag": {"nodes": [{"id": "lane-1", "depends_on": []}]},
            "lanes": [
                {
                    "id": "lane-1",
                    "owner_role": "planner",
                    "scope": "safe delete auto allow",
                    "commands": [
                        setup_cmd,
                        delete_cmd,
                    ],
                }
            ],
            "checkpoints": [{"id": "checkpoint-1", "after_lanes": ["lane-1"], "gate_criteria": ["ok"], "gate_commands": []}],
            "limits": {
                "max_replan": 1,
                "command_guardrails": {
                    "enabled": True,
                    "mode": "human-approval",
                    "denylist_patterns": ["^rmdir\\b", "^rm\\s+-rf\\b"],
                    "approval_safe_path_prefixes": ["./temp", ".\\temp"],
                    "approval_non_interactive_decision": "deny",
                    "phases": ["lane"],
                },
            },
        },
    )
    orch = RuntimeOrchestrator(project_root)
    state = orch.start(runbook_path=runbook, manifest_path=manifest, adapter_name="process-worker", run_id=run_id)
    status = str(state.get("status", "unknown"))
    store = EventStore(project_root)
    events = store.read_events(run_id, limit=0)
    lane_done = [e for e in events if str(e.get("event", "")) == "lane_done"]
    payload = lane_done[-1].get("payload", {}) if lane_done else {}
    evidence = [str(x).strip().lower() for x in payload.get("evidence", [])]
    ok = status == "completed" and any("guardrail-audit:human-approved" in x for x in evidence)
    return ok, f"status={status} evidence={evidence}"


def run_environment_role_override_case(project_root: Path, run_id: str) -> Tuple[bool, str]:
    pe_root = project_root / ".plan-executor"
    runbook = pe_root / "runbooks" / f"{run_id}.json"
    manifest = pe_root / "team-manifests" / f"{run_id}.json"
    build_manifest(manifest)
    write_json(
        runbook,
        {
            "meta": {"generated_at_utc": utc_compact(), "mode": "sequential", "task_type": "code", "max_parallel_workers": 1, "environment": "dev"},
            "dag": {"nodes": [{"id": "lane-1", "depends_on": []}]},
            "lanes": [{"id": "lane-1", "owner_role": "planner", "scope": "env role override", "commands": ["echo override-allowed"]}],
            "checkpoints": [{"id": "checkpoint-1", "after_lanes": ["lane-1"], "gate_criteria": ["ok"], "gate_commands": []}],
            "limits": {
                "max_replan": 1,
                "command_guardrails": {
                    "enabled": True,
                    "environment": "dev",
                    "mode": "enforce",
                    "denylist_patterns": ["^echo\\b"],
                    "environment_policies": {
                        "dev": {
                            "mode": "human-approval",
                            "approval_non_interactive_decision": "deny",
                        }
                    },
                    "role_policies": {
                        "planner": {
                            "mode": "human-approval",
                            "approval_non_interactive_decision": "allow",
                        }
                    },
                    "phases": ["lane"],
                },
            },
        },
    )
    orch = RuntimeOrchestrator(project_root)
    state = orch.start(runbook_path=runbook, manifest_path=manifest, adapter_name="process-worker", run_id=run_id)
    status = str(state.get("status", "unknown"))
    store = EventStore(project_root)
    events = store.read_events(run_id, limit=0)
    lane_done = [e for e in events if str(e.get("event", "")) == "lane_done"]
    payload = lane_done[-1].get("payload", {}) if lane_done else {}
    evidence = [str(x).strip().lower() for x in payload.get("evidence", [])]
    ok = status == "completed" and any("guardrail-audit:human-approved" in x for x in evidence)
    return ok, f"status={status} evidence={evidence}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run command guardrail regression scenarios.")
    parser.add_argument("--project-root", default=".", help="Project root containing .plan-executor")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    print("Guardrails Regression Test")
    print("=" * 36)
    passed = 0

    ok1, d1 = run_lane_block_case(project_root, f"guardrail-lane-{utc_compact()}")
    print(f"[{'PASS' if ok1 else 'FAIL'}] lane-block {d1}")
    if ok1:
        passed += 1

    ok2, d2 = run_gate_block_case(project_root, f"guardrail-gate-{utc_compact()}")
    print(f"[{'PASS' if ok2 else 'FAIL'}] gate-block {d2}")
    if ok2:
        passed += 1

    ok3, d3 = run_profile_template_case(project_root, f"guardrail-profile-{utc_compact()}")
    print(f"[{'PASS' if ok3 else 'FAIL'}] profile-template-block {d3}")
    if ok3:
        passed += 1

    ok4, d4 = run_human_approval_allow_case(project_root, f"guardrail-human-allow-{utc_compact()}")
    print(f"[{'PASS' if ok4 else 'FAIL'}] human-approval-allow {d4}")
    if ok4:
        passed += 1

    ok5, d5 = run_human_approval_deny_case(project_root, f"guardrail-human-deny-{utc_compact()}")
    print(f"[{'PASS' if ok5 else 'FAIL'}] human-approval-deny {d5}")
    if ok5:
        passed += 1

    ok6, d6 = run_safe_path_auto_allow_case(project_root, f"guardrail-safe-path-{utc_compact()}")
    print(f"[{'PASS' if ok6 else 'FAIL'}] safe-path-auto-allow {d6}")
    if ok6:
        passed += 1

    ok7, d7 = run_environment_role_override_case(project_root, f"guardrail-role-env-{utc_compact()}")
    print(f"[{'PASS' if ok7 else 'FAIL'}] role-env-override {d7}")
    if ok7:
        passed += 1

    print("-" * 36)
    print(f"RESULT: {passed}/7 passed")
    return 0 if passed == 7 else 1


if __name__ == "__main__":
    raise SystemExit(main())
