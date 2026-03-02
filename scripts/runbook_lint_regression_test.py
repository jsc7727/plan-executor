#!/usr/bin/env python3
"""Regression tests for runbook lint and mandatory guardrail policy."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

from runtime.runbook_lint import lint_runbook_file


def utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def make_base_runbook(with_guardrails: bool) -> Dict[str, Any]:
    limits: Dict[str, Any] = {"max_replan": 1}
    if with_guardrails:
        limits["command_guardrails"] = {
            "enabled": True,
            "profile": "ci",
            "os_template": "auto",
            "phases": ["lane", "gate"],
        }
    return {
        "meta": {"generated_at_utc": utc_compact(), "mode": "sequential", "task_type": "code", "max_parallel_workers": 1},
        "dag": {"nodes": [{"id": "lane-1", "depends_on": []}]},
        "lanes": [{"id": "lane-1", "owner_role": "planner", "scope": "lint test lane", "commands": ['python -c "print(\'ok\')"']}],
        "checkpoints": [{"id": "checkpoint-1", "after_lanes": ["lane-1"], "gate_criteria": ["ok"], "gate_commands": []}],
        "limits": limits,
    }


def case_missing_guardrail_lint(project_root: Path, run_id: str) -> Tuple[bool, str]:
    runbook = project_root / ".plan-executor" / "runbooks" / f"{run_id}.json"
    write_json(runbook, make_base_runbook(with_guardrails=False))
    lint = lint_runbook_file(runbook, strict=True)
    codes = {str(x.get("code", "")).strip().lower() for x in lint.get("errors", [])}
    ok = (not bool(lint.get("ok", True))) and ("guardrail-required" in codes)
    return ok, f"ok={lint.get('ok')} errors={sorted(codes)}"


def case_profile_guardrail_pass(project_root: Path, run_id: str) -> Tuple[bool, str]:
    runbook = project_root / ".plan-executor" / "runbooks" / f"{run_id}.json"
    write_json(runbook, make_base_runbook(with_guardrails=True))
    lint = lint_runbook_file(runbook, strict=True)
    ok = bool(lint.get("ok", False))
    return ok, f"ok={lint.get('ok')} errors={lint.get('error_count', 0)} warnings={lint.get('warning_count', 0)}"


def case_runtime_cli_enforces_lint(project_root: Path, run_id: str) -> Tuple[bool, str]:
    runbook = project_root / ".plan-executor" / "runbooks" / f"{run_id}.json"
    write_json(runbook, make_base_runbook(with_guardrails=False))
    cli = Path(__file__).resolve().parent / "runtime_cli.py"
    cmd = [
        sys.executable,
        str(cli),
        "--project-root",
        str(project_root),
        "start",
        "--runbook",
        str(runbook),
        "--adapter",
        "process-worker",
        "--run-id",
        run_id,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = f"{proc.stdout}\n{proc.stderr}".lower()
    ok = int(proc.returncode) == 2 and "runbook lint failed" in out
    return ok, f"rc={proc.returncode} lint_failed={'runbook lint failed' in out}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run runbook lint regression scenarios.")
    parser.add_argument("--project-root", default=".", help="Project root containing .plan-executor")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    print("Runbook Lint Regression Test")
    print("=" * 40)

    passed = 0
    ok1, d1 = case_missing_guardrail_lint(project_root, f"lint-missing-{utc_compact()}")
    print(f"[{'PASS' if ok1 else 'FAIL'}] missing-guardrail-lint {d1}")
    if ok1:
        passed += 1

    ok2, d2 = case_profile_guardrail_pass(project_root, f"lint-profile-{utc_compact()}")
    print(f"[{'PASS' if ok2 else 'FAIL'}] profile-guardrail-pass {d2}")
    if ok2:
        passed += 1

    ok3, d3 = case_runtime_cli_enforces_lint(project_root, f"lint-runtime-cli-{utc_compact()}")
    print(f"[{'PASS' if ok3 else 'FAIL'}] runtime-cli-enforces-lint {d3}")
    if ok3:
        passed += 1

    print("-" * 40)
    print(f"RESULT: {passed}/3 passed")
    return 0 if passed == 3 else 1


if __name__ == "__main__":
    raise SystemExit(main())

