#!/usr/bin/env python3
"""Regression tests for daemon/hybrid entrypoint runbook lint enforcement."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple


def utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_cmd(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True)


def make_invalid_runbook() -> Dict[str, Any]:
    return {
        "meta": {"generated_at_utc": utc_compact(), "mode": "sequential", "task_type": "code", "max_parallel_workers": 1},
        "dag": {"nodes": [{"id": "lane-1", "depends_on": []}]},
        "lanes": [{"id": "lane-1", "owner_role": "planner", "scope": "invalid lint runbook", "commands": ['python -c "print(\'ok\')"']}],
        "checkpoints": [{"id": "checkpoint-1", "after_lanes": ["lane-1"], "gate_criteria": ["ok"], "gate_commands": []}],
        "limits": {"max_replan": 1},
    }


def case_daemon_enqueue_rejects_invalid(project_root: Path, run_id: str) -> Tuple[bool, str]:
    skill_scripts = Path(__file__).resolve().parent
    runbook = project_root / ".plan-executor" / "runbooks" / f"{run_id}.json"
    write_json(runbook, make_invalid_runbook())

    cmd = [
        sys.executable,
        str(skill_scripts / "runtime_daemon_cli.py"),
        "--project-root",
        str(project_root),
        "enqueue",
        "--runbook",
        str(runbook),
        "--adapter",
        "process-worker",
    ]
    proc = run_cmd(cmd)
    out = f"{proc.stdout}\n{proc.stderr}".lower()
    ok = int(proc.returncode) == 1 and "runbook lint failed" in out
    return ok, f"rc={proc.returncode} lint_failed={'runbook lint failed' in out}"


def case_daemon_run_once_rejects_manual_bypass(project_root: Path, run_id: str) -> Tuple[bool, str]:
    skill_scripts = Path(__file__).resolve().parent
    pe_root = project_root / ".plan-executor"
    runbook = pe_root / "runbooks" / f"{run_id}.json"
    write_json(runbook, make_invalid_runbook())
    pending = pe_root / "queue" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    manual_job = pending / f"manual-{run_id}.json"
    write_json(
        manual_job,
        {
            "job_id": f"manual-{run_id}",
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "runbook": str(runbook.resolve()),
            "manifest": "",
            "adapter": "process-worker",
            "run_id": run_id,
        },
    )

    cmd = [
        sys.executable,
        str(skill_scripts / "runtime_daemon_cli.py"),
        "--project-root",
        str(project_root),
        "run-once",
        "--max-jobs",
        "1",
    ]
    proc = run_cmd(cmd)
    out = f"{proc.stdout}\n{proc.stderr}".lower()
    failed_bucket = pe_root / "queue" / "failed"
    failed_files = list(failed_bucket.glob(f"*{run_id}*.json"))
    ok = int(proc.returncode) == 0 and "failed=1" in out and len(failed_files) >= 1
    return ok, f"rc={proc.returncode} failed_flag={'failed=1' in out} failed_files={len(failed_files)}"


def case_hybrid_prepare_rejects_invalid_guardrail(project_root: Path, run_id: str) -> Tuple[bool, str]:
    skill_scripts = Path(__file__).resolve().parent
    pe_root = project_root / ".plan-executor"
    plan = pe_root / "frontstage" / f"invalid-plan-{run_id}.json"
    write_json(
        plan,
        {
            "stages": [
                {"name": "plan", "owner_role": "planner"},
                {"name": "build", "owner_role": "frontend"},
            ],
            "limits": {
                "command_guardrails": {
                    "enabled": False,
                    "profile": "ci",
                }
            },
        },
    )
    out_runbook = pe_root / "runbooks" / f"hybrid-invalid-{run_id}.json"
    out_manifest = pe_root / "team-manifests" / f"hybrid-invalid-{run_id}.json"
    cmd = [
        sys.executable,
        str(skill_scripts / "hybrid_pipeline.py"),
        "--project-root",
        str(project_root),
        "--frontstage-plan",
        str(plan),
        "--prepare-only",
        "--run-id",
        run_id,
        "--runbook-out",
        str(out_runbook),
        "--manifest-out",
        str(out_manifest),
    ]
    proc = run_cmd(cmd)
    out = f"{proc.stdout}\n{proc.stderr}".lower()
    ok = int(proc.returncode) == 2 and "runbook lint failed" in out
    return ok, f"rc={proc.returncode} lint_failed={'runbook lint failed' in out}"


def case_hybrid_prepare_accepts_default_guardrail(project_root: Path, run_id: str) -> Tuple[bool, str]:
    skill_scripts = Path(__file__).resolve().parent
    pe_root = project_root / ".plan-executor"
    plan = pe_root / "frontstage" / f"valid-plan-{run_id}.json"
    write_json(
        plan,
        {
            "stages": [
                {"name": "plan", "owner_role": "planner"},
                {"name": "build", "owner_role": "frontend"},
                {"name": "verify", "owner_role": "qa"},
            ]
        },
    )
    out_runbook = pe_root / "runbooks" / f"hybrid-valid-{run_id}.json"
    out_manifest = pe_root / "team-manifests" / f"hybrid-valid-{run_id}.json"
    cmd = [
        sys.executable,
        str(skill_scripts / "hybrid_pipeline.py"),
        "--project-root",
        str(project_root),
        "--frontstage-plan",
        str(plan),
        "--prepare-only",
        "--run-id",
        run_id,
        "--runbook-out",
        str(out_runbook),
        "--manifest-out",
        str(out_manifest),
    ]
    proc = run_cmd(cmd)
    if int(proc.returncode) != 0 or (not out_runbook.exists()):
        return False, f"rc={proc.returncode} runbook_exists={out_runbook.exists()}"
    payload = json.loads(out_runbook.read_text(encoding="utf-8-sig"))
    guardrails = payload.get("limits", {}).get("command_guardrails", {})
    ok = bool(isinstance(guardrails, dict) and guardrails.get("profile") == "ci")
    return ok, f"rc={proc.returncode} guardrail_profile={guardrails.get('profile', '')}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daemon/hybrid lint entrypoint regression scenarios.")
    parser.add_argument("--project-root", default=".", help="Project root containing .plan-executor")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    print("Entrypoint Lint Regression Test")
    print("=" * 42)
    passed = 0

    ok1, d1 = case_daemon_enqueue_rejects_invalid(project_root, f"entry-enqueue-{utc_compact()}")
    print(f"[{'PASS' if ok1 else 'FAIL'}] daemon-enqueue-lint {d1}")
    if ok1:
        passed += 1

    ok2, d2 = case_daemon_run_once_rejects_manual_bypass(project_root, f"entry-runonce-{utc_compact()}")
    print(f"[{'PASS' if ok2 else 'FAIL'}] daemon-run-once-lint {d2}")
    if ok2:
        passed += 1

    ok3, d3 = case_hybrid_prepare_rejects_invalid_guardrail(project_root, f"entry-hybrid-bad-{utc_compact()}")
    print(f"[{'PASS' if ok3 else 'FAIL'}] hybrid-prepare-lint-fail {d3}")
    if ok3:
        passed += 1

    ok4, d4 = case_hybrid_prepare_accepts_default_guardrail(project_root, f"entry-hybrid-good-{utc_compact()}")
    print(f"[{'PASS' if ok4 else 'FAIL'}] hybrid-prepare-lint-pass {d4}")
    if ok4:
        passed += 1

    print("-" * 42)
    print(f"RESULT: {passed}/4 passed")
    return 0 if passed == 4 else 1


if __name__ == "__main__":
    raise SystemExit(main())

