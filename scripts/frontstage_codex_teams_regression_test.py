#!/usr/bin/env python3
"""Regression test for frontstage_codex_teams planner with mock agents."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run frontstage codex teams regression test.")
    parser.add_argument("--project-root", default=".", help="Project root")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    skill_scripts = Path(__file__).resolve().parent
    planner_cli = skill_scripts / "frontstage_codex_teams.py"
    if not planner_cli.exists():
        print(f"[FAIL] missing planner script: {planner_cli}")
        return 1

    pe_root = project_root / ".plan-executor"
    test_root = pe_root / "frontstage" / "tests"
    mock_agent = test_root / "mock_frontstage_agent.py"
    out_path = test_root / f"frontstage-plan-{utc_compact()}.json"

    write_text(
        mock_agent,
        """
import argparse
import json
import time

parser = argparse.ArgumentParser()
parser.add_argument("--role", required=True)
parser.add_argument("--phase", required=True)
parser.add_argument("--round", required=True)
parser.add_argument("--objective", required=True)
args = parser.parse_args()

role = args.role.strip().lower()
phase = args.phase.strip().lower()
rnd = int(args.round)
time.sleep(0.2)

if phase == "critique":
    payload = {
        "critiques": [
            {
                "proposal_id": f"{role}-core",
                "severity": "medium",
                "content": f"{role} critique round={rnd}"
            }
        ],
        "votes": [],
        "notes": [f"{role} critique round={rnd}"]
    }
elif phase == "revise":
    payload = {
        "proposals": [
            {
                "proposal_id": f"{role}-core",
                "name": f"{role}-stage-revised",
                "owner_role": role,
                "summary": f"{role} revised plan for {args.objective}",
                "commands": [f"echo {role}-revise-r{rnd}"]
            }
        ],
        "votes": [
            {
                "proposal_id": f"{role}-core",
                "decision": "approve",
                "confidence": 0.9
            }
        ],
        "notes": [f"{role} revise round={rnd}"]
    }
else:
    payload = {
        "proposals": [
            {
                "proposal_id": f"{role}-core",
                "name": f"{role}-stage",
                "owner_role": role,
                "summary": f"{role} plan for {args.objective}",
                "commands": [f"echo {role}-r{rnd}"]
            }
        ],
        "votes": [],
        "notes": [f"{role} propose round={rnd}"]
    }
print(json.dumps(payload))
""".strip()
        + "\n",
    )

    cmd = [
        sys.executable,
        str(planner_cli),
        "--project-root",
        str(project_root),
        "--objective",
        "frontstage regression objective",
        "--roles",
        "planner,frontend,qa",
        "--rounds",
        "2",
        "--max-parallel-agents",
        "3",
        "--agent-runtime",
        "persistent",
        "--worker-memory-lines",
        "20",
        "--accept-threshold",
        "0.0",
        "--quorum-ratio",
        "0.0",
        "--max-stages",
        "6",
        "--output",
        str(out_path),
        "--skip-codex-check",
        "--agent-cmd-template",
        f'{sys.executable} "{mock_agent}" --role "{{role}}" --phase "{{phase}}" --round "{{round_index}}" --objective "{{objective}}"',
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(project_root))

    if proc.returncode != 0:
        print("[FAIL] planner exited non-zero")
        print(proc.stdout[-2000:])
        print(proc.stderr[-2000:])
        return 1
    if not out_path.exists():
        print(f"[FAIL] output not found: {out_path}")
        return 1

    payload = read_json(out_path)
    stages = payload.get("stages", [])
    consensus = payload.get("consensus", {})
    trace = payload.get("trace", {})
    calls = trace.get("agent_calls", [])
    phase_counts = trace.get("phase_counts", {}) if isinstance(trace, dict) else {}
    critique_count = int(consensus.get("critique_count", 0))

    ok = (
        isinstance(stages, list)
        and len(stages) >= 3
        and isinstance(consensus, dict)
        and int(consensus.get("candidate_count", 0)) >= 3
        and isinstance(calls, list)
        and len(calls) >= 18
        and int(phase_counts.get("propose", 0)) > 0
        and int(phase_counts.get("critique", 0)) > 0
        and int(phase_counts.get("revise", 0)) > 0
        and critique_count > 0
    )

    print("Frontstage Codex Teams Regression Test")
    print("=" * 44)
    print(f"returncode={proc.returncode}")
    print(f"output={out_path}")
    print(
        f"stages={len(stages)} candidate_count={consensus.get('candidate_count', 0)} "
        f"agent_calls={len(calls)} phase_counts={phase_counts} critique_count={critique_count}"
    )
    print(f"RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
