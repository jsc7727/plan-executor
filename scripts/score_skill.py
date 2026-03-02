#!/usr/bin/env python3
"""Score plan-executor skill quality with a simple 100-point rubric."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List


@dataclass
class Criterion:
    name: str
    max_points: int
    check: Callable[[str, str, Path], bool]
    fail_hint: str


def has_all(text: str, patterns: List[str]) -> bool:
    return all(re.search(p, text, flags=re.IGNORECASE | re.MULTILINE) for p in patterns)


def files_exist(root: Path, rel_paths: List[str]) -> bool:
    return all((root / rel).exists() for rel in rel_paths)


def count_arxiv_links(text: str) -> int:
    return len(re.findall(r"https://arxiv\.org/abs/\d{4}\.\d{4,5}", text))


def build_criteria() -> List[Criterion]:
    return [
        Criterion(
            "Core workflow completeness",
            15,
            lambda s, r, d: has_all(
                s,
                [
                    r"###\s*1\.\s*Frame the objective",
                    r"###\s*2\.\s*Create an execution plan",
                    r"###\s*3\.\s*Start executing immediately",
                    r"###\s*4\.\s*Keep a rolling plan",
                    r"###\s*5\.\s*Replan on blockers",
                    r"###\s*6\.\s*Verify and close",
                ],
            ),
            "Workflow steps 1..6 are missing or renamed.",
        ),
        Criterion(
            "Execution-first behavior",
            10,
            lambda s, r, d: has_all(
                s,
                [
                    r"default to action after planning",
                    r"do not stop at plan-only output",
                ],
            ),
            "Execution-first guardrails are weak.",
        ),
        Criterion(
            "Task branching coverage",
            10,
            lambda s, r, d: has_all(
                s,
                [
                    r"Path:\s*code",
                    r"Path:\s*document",
                    r"Path:\s*research",
                ],
            ),
            "Code/document/research branch coverage is incomplete.",
        ),
        Criterion(
            "Parallel safety model",
            12,
            lambda s, r, d: has_all(
                s,
                [
                    r"##\s*Parallel Execution Mode",
                    r"When to enable",
                    r"Synchronization rules",
                    r"pause parallel mode and return to sequential execution",
                ],
            ),
            "Parallel execution guardrails are incomplete.",
        ),
        Criterion(
            "Multi-agent emulation protocol",
            8,
            lambda s, r, d: has_all(
                s,
                [
                    r"##\s*Multi-Agent Emulation Mode",
                    r"Capability boundary",
                    r"Role model",
                    r"orchestrator",
                    r"integrator",
                ],
            ),
            "Role split and boundary are not explicit enough.",
        ),
        Criterion(
            "Verification rigor",
            10,
            lambda s, r, d: has_all(
                s,
                [
                    r"Require external feedback for correction",
                    r"test result",
                    r"compiler/linter output",
                    r"tool execution result",
                ],
            ),
            "External evidence gates for correction are missing.",
        ),
        Criterion(
            "Observability metrics",
            5,
            lambda s, r, d: has_all(
                s,
                [
                    r"stall_rounds",
                    r"replan_count",
                    r"verification_pass_rate",
                    r"merge_conflicts",
                ],
            ),
            "Operational metrics are missing.",
        ),
        Criterion(
            "Paper-backed reference depth",
            10,
            lambda s, r, d: "orchestrator-papers.md" in s and count_arxiv_links(r) >= 12,
            "References are too thin (need >=12 arXiv links).",
        ),
        Criterion(
            "Operations layer readiness",
            10,
            lambda s, r, d: has_all(
                s,
                [
                    r"Operations Layer \(OMC-Inspired, Skill-Native\)",
                    r"Execution profiles",
                    r"Event hooks",
                    r"Escalation matrix",
                    r"Runbook bootstrap",
                    r"scripts/bootstrap_runbook.py",
                    r"Team pipeline compatibility modes",
                    r"teams-pipeline",
                    r"swarm-style",
                    r"ultrapilot-style",
                    r"Worker runtime adapters",
                    r"Message bus envelope",
                    r"Worker watchdog",
                    r"process-worker",
                    r"ai-worker",
                    r"Independent runtime CLI",
                    r"runtime_cli.py",
                    r"\.plan-executor/state/",
                    r"Queue daemon and operations dashboard",
                    r"runtime_daemon_cli.py",
                    r"runtime_dashboard.py",
                    r"runtime_control_cli.py",
                    r"max_parallel_workers",
                    r"gate_commands",
                    r"command_guardrails|guardrail",
                    r"runbook lint|runtime_runbook_lint\.py",
                    r"guardrail profile|dev|ci|prod",
                    r"os template|windows|linux|darwin",
                    r"human-approval|approval_non_interactive_decision|approval_auto_allow_patterns",
                    r"approval_safe_path|safe-delete|safe path",
                    r"role_policies|environment_policies|guardrail environment",
                    r"runtime_daemon_cli\.py",
                    r"enqueue",
                    r"hybrid_pipeline\.py",
                    r"runbook lint failed after normalization|skip-runbook-lint",
                    r"recover --stale-sec",
                    r"Non-core utilities",
                    r"runtime_maintenance.py",
                    r"runtime_report.py",
                    r"runtime_benchmark.py",
                ],
            ),
            "Operations layer controls are incomplete (profiles/hooks/escalation/runbook).",
        ),
        Criterion(
            "Runtime collaboration primitives and matrix",
            10,
            lambda s, r, d: has_all(
                s,
                [
                    r"agent_runtime_cli\.py",
                    r"Message bus envelope",
                    r"consensus",
                    r"evaluate",
                    r"reject-threshold",
                    r"required-roles",
                    r"single-winner",
                    r"delegate-worker",
                    r"runtime_delegate_cli\.py",
                    r"candidate plans",
                    r"consensus evaluate",
                    r"auto-create|auto create",
                    r"synthetic",
                    r"template",
                    r"Capability Matrix",
                    r"PE v2",
                    r"OMC",
                    r"ai-worker",
                    r"Hybrid bridge",
                    r"hybrid_pipeline\.py",
                    r"frontstage-plan",
                    r"frontstage.*codex.*teams|frontstage_codex_teams\.py",
                    r"debate-mode|critique-revise",
                    r"agent-runtime|persistent",
                    r"IPC",
                    r"consensus-patch|consensus_reconfigure|reconfigure",
                ],
            )
            and files_exist(
                d,
                [
                    "scripts/agent_runtime_cli.py",
                    "scripts/runtime/specialist_registry.py",
                    "scripts/runtime/message_bus.py",
                    "scripts/runtime/consensus_engine.py",
                    "scripts/runtime/delegate_bus.py",
                    "scripts/runtime/delegate_worker.py",
                    "scripts/runtime_delegate_cli.py",
                    "scripts/runtime/plan_search.py",
                    "scripts/runtime_plan_cli.py",
                    "scripts/runtime/gate_engine.py",
                    "scripts/runtime/command_guardrails.py",
                    "scripts/runtime/runbook_lint.py",
                    "scripts/runtime_runbook_lint.py",
                    "scripts/runtime/consensus_templates.py",
                    "scripts/bootstrap_consensus_template.py",
                    "scripts/ai_worker_regression_test.py",
                    "scripts/delegate_worker_regression_test.py",
                    "scripts/guardrails_regression_test.py",
                    "scripts/runbook_lint_regression_test.py",
                    "scripts/entrypoint_lint_regression_test.py",
                    "scripts/consensus_regression_test.py",
                    "scripts/plan_search_regression_test.py",
                    "scripts/consensus_checkpoint_regression_test.py",
                    "scripts/consensus_autocreate_regression_test.py",
                    "scripts/consensus_template_regression_test.py",
                    "scripts/hybrid_pipeline.py",
                    "scripts/frontstage_codex_teams.py",
                    "scripts/frontstage_codex_teams_regression_test.py",
                    "scripts/frontstage_role_worker.py",
                    "scripts/runtime/control_plane.py",
                    "scripts/runtime_control_cli.py",
                    "scripts/ipc_control_regression_test.py",
                    "scripts/consensus_reconfigure_regression_test.py",
                    "scripts/runtime_benchmark.py",
                ],
            ),
            "Role registry/message bus/consensus/matrix linkage is incomplete.",
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Score a plan-executor skill on a 100-point rubric.")
    parser.add_argument(
        "--skill-dir",
        default=str(Path(__file__).resolve().parents[1]),
        help="Path to skill directory containing SKILL.md and references/orchestrator-papers.md",
    )
    args = parser.parse_args()

    skill_dir = Path(args.skill_dir)
    skill_md_path = skill_dir / "SKILL.md"
    refs_path = skill_dir / "references" / "orchestrator-papers.md"

    if not skill_md_path.exists():
        print(f"[ERROR] Missing file: {skill_md_path}")
        return 2
    if not refs_path.exists():
        print(f"[ERROR] Missing file: {refs_path}")
        return 2

    skill_md = skill_md_path.read_text(encoding="utf-8")
    refs_md = refs_path.read_text(encoding="utf-8")

    criteria = build_criteria()
    total = 0
    print("Plan Executor Skill Scorecard")
    print("-" * 40)
    for c in criteria:
        ok = c.check(skill_md, refs_md, skill_dir)
        pts = c.max_points if ok else 0
        total += pts
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {c.name}: {pts}/{c.max_points}")
        if not ok:
            print(f"       Hint: {c.fail_hint}")

    print("-" * 40)
    print(f"TOTAL: {total}/100")

    if total >= 90:
        grade = "A"
    elif total >= 80:
        grade = "B"
    elif total >= 70:
        grade = "C"
    elif total >= 60:
        grade = "D"
    else:
        grade = "F"

    print(f"GRADE: {grade}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
