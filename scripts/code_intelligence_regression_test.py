#!/usr/bin/env python3
"""Regression tests for code-intelligence lane impact analysis."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

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
                {
                    "id": "worker-1",
                    "role": "planner",
                    "command_template": "{cmd}",
                    "timeout_sec": 60,
                    "max_retries": 1,
                    "backoff_sec": 1.0,
                }
            ],
        },
    )


def python_write_cmd(rel_path: str, content: str) -> str:
    script = (
        "from pathlib import Path; "
        f"p=Path({rel_path!r}); "
        "p.parent.mkdir(parents=True, exist_ok=True); "
        f"p.write_text({content!r}, encoding='utf-8')"
    )
    return f'python -c "{script.replace("\\\\", "\\\\\\\\").replace(\'"\', \'\\\\\\"\')}"'


def _last_lane_payload(project_root: Path, run_id: str) -> Dict[str, Any]:
    store = EventStore(project_root)
    events = store.read_events(run_id, limit=0)
    rows = [e for e in events if str(e.get("event", "")) == "lane_done"]
    return dict(rows[-1].get("payload", {})) if rows else {}


def _find_code_intel_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    commands = payload.get("commands", [])
    if not isinstance(commands, list):
        return {}
    for row in reversed(commands):
        if isinstance(row, dict) and str(row.get("kind", "")).strip().lower() == "code-intelligence":
            result = row.get("result", {})
            return dict(result) if isinstance(result, dict) else {}
    return {}


def _base_runbook(
    run_id: str,
    lane_command: str,
    code_intel: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "meta": {
            "generated_at_utc": utc_compact(),
            "mode": "sequential",
            "task_type": "code",
            "max_parallel_workers": 1,
            "profile": "balanced",
            "environment": "ci",
        },
        "dag": {"nodes": [{"id": "lane-1", "depends_on": []}]},
        "lanes": [
            {
                "id": "lane-1",
                "owner_role": "planner",
                "scope": f"code-intel regression {run_id}",
                "commands": [lane_command],
            }
        ],
        "checkpoints": [
            {
                "id": "checkpoint-1",
                "after_lanes": ["lane-1"],
                "gate_criteria": ["ok"],
                "gate_commands": [],
            }
        ],
        "limits": {
            "max_replan": 1,
            "command_guardrails": {
                "enabled": True,
                "mode": "enforce",
                "allowlist_patterns": [r"^python(\.exe)?\s+-c\b"],
                "phases": ["lane", "gate"],
                "code_intelligence": code_intel,
            },
        },
    }


def case_audit_mode_reports_violation(project_root: Path, run_id: str) -> Tuple[bool, str]:
    pe_root = project_root / ".plan-executor"
    runbook = pe_root / "runbooks" / f"{run_id}.json"
    manifest = pe_root / "team-manifests" / f"{run_id}.json"
    build_manifest(manifest)
    cmd = python_write_cmd(
        "temp/code-intel/audit_case.py",
        "def a():\n    return 1\n\ndef b():\n    return 2\n\ndef c():\n    return 3\n",
    )
    write_json(
        runbook,
        _base_runbook(
            run_id=run_id,
            lane_command=cmd,
            code_intel={
                "enabled": True,
                "mode": "audit",
                "high_risk_symbol_threshold": 2,
                "max_high_risk_files": 0,
            },
        ),
    )
    orch = RuntimeOrchestrator(project_root)
    state = orch.start(runbook_path=runbook, manifest_path=manifest, adapter_name="process-worker", run_id=run_id)
    status = str(state.get("status", "unknown"))
    payload = _last_lane_payload(project_root, run_id)
    evidence = [str(x).strip().lower() for x in payload.get("evidence", [])]
    ci = _find_code_intel_result(payload)
    ok = (
        status == "completed"
        and "code-intel-applied" in evidence
        and "code-intel-violation" in evidence
        and bool(ci.get("applied", False))
        and bool(ci.get("violation", False))
        and not bool(ci.get("should_block", False))
    )
    return ok, f"status={status} evidence={evidence} ci_violation={ci.get('violation', False)}"


def case_enforce_mode_blocks_violation(project_root: Path, run_id: str) -> Tuple[bool, str]:
    pe_root = project_root / ".plan-executor"
    runbook = pe_root / "runbooks" / f"{run_id}.json"
    manifest = pe_root / "team-manifests" / f"{run_id}.json"
    build_manifest(manifest)
    cmd = python_write_cmd(
        "temp/code-intel/enforce_case.py",
        "def f1():\n    return 1\n\ndef f2():\n    return 2\n",
    )
    write_json(
        runbook,
        _base_runbook(
            run_id=run_id,
            lane_command=cmd,
            code_intel={
                "enabled": True,
                "mode": "enforce",
                "high_risk_symbol_threshold": 1,
                "max_high_risk_files": 0,
            },
        ),
    )
    orch = RuntimeOrchestrator(project_root)
    state = orch.start(runbook_path=runbook, manifest_path=manifest, adapter_name="process-worker", run_id=run_id)
    status = str(state.get("status", "unknown"))
    payload = _last_lane_payload(project_root, run_id)
    evidence = [str(x).strip().lower() for x in payload.get("evidence", [])]
    err = str(payload.get("error", "")).strip().lower()
    ci = _find_code_intel_result(payload)
    ok = (
        status == "failed"
        and "code-intel-blocked" in evidence
        and "code-intel-violation" in evidence
        and "code intelligence blocked lane" in err
        and bool(ci.get("should_block", False))
    )
    return ok, f"status={status} evidence={evidence} should_block={ci.get('should_block', False)} err={err[:140]}"


def case_typescript_parsing_path(project_root: Path, run_id: str) -> Tuple[bool, str]:
    pe_root = project_root / ".plan-executor"
    runbook = pe_root / "runbooks" / f"{run_id}.json"
    manifest = pe_root / "team-manifests" / f"{run_id}.json"
    build_manifest(manifest)
    cmd = python_write_cmd(
        "temp/code-intel/ts_case.ts",
        "import { x } from './x';\nexport const A = 1;\nexport function makeB() { return x + 1; }\n",
    )
    write_json(
        runbook,
        _base_runbook(
            run_id=run_id,
            lane_command=cmd,
            code_intel={
                "enabled": True,
                "mode": "enforce",
                "high_risk_symbol_threshold": 20,
                "max_high_risk_files": 1,
            },
        ),
    )
    orch = RuntimeOrchestrator(project_root)
    state = orch.start(runbook_path=runbook, manifest_path=manifest, adapter_name="process-worker", run_id=run_id)
    status = str(state.get("status", "unknown"))
    payload = _last_lane_payload(project_root, run_id)
    ci = _find_code_intel_result(payload)
    findings = ci.get("findings", [])
    first_lang = ""
    if isinstance(findings, list) and findings and isinstance(findings[0], dict):
        first_lang = str(findings[0].get("language", "")).strip().lower()
    ok = status == "completed" and bool(ci.get("applied", False)) and first_lang == "typescript"
    return ok, f"status={status} applied={ci.get('applied', False)} first_lang={first_lang}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run code intelligence regression scenarios.")
    parser.add_argument("--project-root", default=".", help="Project root containing .plan-executor")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    print("Code Intelligence Regression Test")
    print("=" * 42)
    passed = 0

    ok1, d1 = case_audit_mode_reports_violation(project_root, f"code-intel-audit-{utc_compact()}")
    print(f"[{'PASS' if ok1 else 'FAIL'}] audit-mode-violation {d1}")
    if ok1:
        passed += 1

    ok2, d2 = case_enforce_mode_blocks_violation(project_root, f"code-intel-enforce-{utc_compact()}")
    print(f"[{'PASS' if ok2 else 'FAIL'}] enforce-mode-block {d2}")
    if ok2:
        passed += 1

    ok3, d3 = case_typescript_parsing_path(project_root, f"code-intel-ts-{utc_compact()}")
    print(f"[{'PASS' if ok3 else 'FAIL'}] typescript-analysis {d3}")
    if ok3:
        passed += 1

    print("-" * 42)
    print(f"RESULT: {passed}/3 passed")
    return 0 if passed == 3 else 1


if __name__ == "__main__":
    raise SystemExit(main())

