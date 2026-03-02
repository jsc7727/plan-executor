#!/usr/bin/env python3
"""Scenario stress tests for plan-executor SKILL.md.

This is a heuristic test harness. It checks whether the skill text contains
instructions needed to handle practical orchestration scenarios.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class TestCase:
    scenario: str
    severity: str
    patterns: List[str]
    why_it_matters: str
    recommendation: str
    required_files: List[str] = field(default_factory=list)


def has_all(text: str, patterns: List[str]) -> bool:
    return all(re.search(p, text, flags=re.IGNORECASE | re.MULTILINE) for p in patterns)


def build_cases() -> List[TestCase]:
    return [
        TestCase(
            scenario="Code fix with validation gates",
            severity="high",
            patterns=[
                r"Path:\s*code",
                r"targeted validation",
                r"Verify and close",
            ],
            why_it_matters="Code tasks regress quickly without explicit validation.",
            recommendation="Keep targeted->broad validation and list exact commands in results.",
        ),
        TestCase(
            scenario="Parallel lanes with merge checkpoints",
            severity="high",
            patterns=[
                r"Parallel Execution Mode",
                r"merge checkpoints",
                r"return to sequential execution",
            ],
            why_it_matters="Parallelism without merge gates causes integration failures.",
            recommendation="Keep lane ownership and checkpoint synchronization strict.",
        ),
        TestCase(
            scenario="Research output quality",
            severity="medium",
            patterns=[
                r"Path:\s*research",
                r"primary documentation",
                r"dated sources",
            ],
            why_it_matters="Research answers degrade without source quality rules.",
            recommendation="Keep source-quality requirements and explicit tradeoff comparison.",
        ),
        TestCase(
            scenario="Hard-task plan search",
            severity="medium",
            patterns=[
                r"candidate plans",
                r"fallback plan",
            ],
            why_it_matters="Single-plan execution is brittle under uncertainty.",
            recommendation="Retain 2-3 candidate plan generation on hard tasks.",
        ),
        TestCase(
            scenario="External evidence for self-correction",
            severity="high",
            patterns=[
                r"external signal",
                r"test result",
                r"compiler/linter output",
                r"tool execution result",
            ],
            why_it_matters="Self-correction is unreliable without outside feedback.",
            recommendation="Require tool/test evidence before accepting corrections.",
        ),
        TestCase(
            scenario="Missing explicit max replan limit",
            severity="high",
            patterns=[
                r"max replan|max_replan|replan limit|maximum replans",
            ],
            why_it_matters="Unlimited replanning can stall delivery indefinitely.",
            recommendation="Add a hard limit such as max 2-3 replans before escalation.",
        ),
        TestCase(
            scenario="Missing agent card schema template",
            severity="medium",
            patterns=[
                r"```(json|yaml|markdown)",
                r"agent card",
                r"output contract",
            ],
            why_it_matters="Without a fixed schema, lane handoffs become inconsistent.",
            recommendation="Add a minimal agent-card template with fixed fields.",
        ),
        TestCase(
            scenario="Missing merge conflict decision matrix",
            severity="medium",
            patterns=[
                r"conflict matrix|decision matrix|precedence rule|tie-break",
            ],
            why_it_matters="Conflicts recur if there is no deterministic tie-break policy.",
            recommendation="Add precedence rules (owner wins, integrator override, fallback).",
        ),
        TestCase(
            scenario="Missing dependency format example",
            severity="low",
            patterns=[
                r"DAG",
                r"adjacency|node|edge|depends_on",
                r"```(json|yaml)",
            ],
            why_it_matters="Teams execute faster when dependencies are expressed in a fixed format.",
            recommendation="Include a small DAG example for lane scheduling.",
        ),
        TestCase(
            scenario="Missing checkpoint acceptance criteria",
            severity="high",
            patterns=[
                r"checkpoint",
                r"acceptance criteria|exit criteria|gate criteria",
            ],
            why_it_matters="Checkpoints fail as quality gates without explicit pass criteria.",
            recommendation="Define pass/fail criteria per checkpoint (tests, lint, artifact shape).",
        ),
        TestCase(
            scenario="Paper-backed role presets for PM/Design/FE/BE/QA",
            severity="high",
            patterns=[
                r"Paper-backed role presets",
                r"Preset D:\s*`product-web-app`",
                r"planner",
                r"designer",
                r"frontend",
                r"backend",
                r"qa",
                r"Paper basis",
            ],
            why_it_matters="Practical multi-role orchestration needs domain-role presets with citation traceability.",
            recommendation="Add PM/Designer/FE/BE/QA preset and cite paper basis per preset.",
        ),
        TestCase(
            scenario="Operations layer profile/hook/escalation controls",
            severity="high",
            patterns=[
                r"Operations Layer \(OMC-Inspired, Skill-Native\)",
                r"Execution profiles",
                r"Event hooks",
                r"Escalation matrix",
            ],
            why_it_matters="Without operational controls, orchestration quality is inconsistent under pressure.",
            recommendation="Define profiles, hook lifecycle, and hard escalation triggers.",
        ),
        TestCase(
            scenario="Runbook bootstrap automation wired",
            severity="high",
            patterns=[
                r"Runbook bootstrap",
                r"scripts/bootstrap_runbook.py",
            ],
            required_files=[
                "scripts/bootstrap_runbook.py",
            ],
            why_it_matters="Large runs need deterministic artifacts, not ad-hoc lane notes.",
            recommendation="Provide a script that generates preset/profile-aware runbook artifacts.",
        ),
        TestCase(
            scenario="OMC-style compatibility modes added",
            severity="high",
            patterns=[
                r"Team pipeline compatibility modes",
                r"teams-pipeline",
                r"swarm-style",
                r"ultrapilot-style",
            ],
            why_it_matters="Depth parity needs explicit orchestration topologies, not one generic parallel mode.",
            recommendation="Add mode-specific rules for teams/swarm/ultrapilot operation.",
        ),
        TestCase(
            scenario="Team manifest bootstrap automation wired",
            severity="high",
            patterns=[
                r"Worker runtime adapters",
                r"Message bus envelope",
                r"Worker watchdog",
            ],
            required_files=[
                "scripts/bootstrap_team_manifest.py",
            ],
            why_it_matters="Worker topology and runtime adapter must be captured as an executable artifact.",
            recommendation="Provide team-manifest bootstrap script and runtime fallback behavior.",
        ),
        TestCase(
            scenario="Independent runtime CLI present",
            severity="high",
            patterns=[
                r"Independent runtime CLI",
                r"runtime_cli.py",
                r"start",
                r"status",
                r"resume",
                r"abort",
                r"\.plan-executor/state/",
            ],
            required_files=[
                "scripts/runtime_cli.py",
                "scripts/runtime/orchestrator.py",
                "scripts/runtime/event_store.py",
                "scripts/runtime/worker_adapters.py",
            ],
            why_it_matters="Without a resumable runtime CLI, orchestration remains documentation-level only.",
            recommendation="Provide start/status/resume/abort commands with persisted state and events.",
        ),
        TestCase(
            scenario="Queue daemon scheduler present",
            severity="high",
            patterns=[
                r"Queue daemon and operations dashboard",
                r"runtime_daemon_cli\.py",
                r"enqueue",
                r"run-once",
                r"stats",
            ],
            required_files=[
                "scripts/runtime/daemon.py",
                "scripts/runtime_daemon_cli.py",
            ],
            why_it_matters="Independent multi-runtime needs queued scheduling beyond one-shot foreground runs.",
            recommendation="Add daemon queue commands for enqueue/process/serve/stats.",
        ),
        TestCase(
            scenario="Operations dashboard present",
            severity="medium",
            patterns=[
                r"runtime_dashboard\.py",
            ],
            required_files=[
                "scripts/runtime_dashboard.py",
            ],
            why_it_matters="Without runtime visibility, operators cannot monitor queue/run health efficiently.",
            recommendation="Add dashboard command for run/queue/state overview.",
        ),
        TestCase(
            scenario="True parallel scheduler controls present",
            severity="high",
            patterns=[
                r"max_parallel_workers",
                r"concurrently",
            ],
            required_files=[
                "scripts/runtime/orchestrator.py",
            ],
            why_it_matters="Without DAG-ready concurrent scheduling, runtime remains effectively sequential.",
            recommendation="Add max_parallel_workers-driven concurrent scheduling for ready nodes.",
        ),
        TestCase(
            scenario="Executable gate engine present",
            severity="high",
            patterns=[
                r"gate_commands",
                r"Checkpoint gates evaluate",
            ],
            required_files=[
                "scripts/runtime/gate_engine.py",
                "scripts/bootstrap_runbook.py",
            ],
            why_it_matters="Criteria-only checkpoints cannot enforce actual quality gates.",
            recommendation="Execute gate commands and fail run on non-zero exit.",
        ),
        TestCase(
            scenario="Command guardrails enforce allow/deny policy",
            severity="high",
            patterns=[
                r"command_guardrails|guardrail",
                r"allowlist|denylist",
            ],
            required_files=[
                "scripts/runtime/command_guardrails.py",
                "scripts/runtime/worker_adapters.py",
                "scripts/runtime/gate_engine.py",
                "scripts/guardrails_regression_test.py",
            ],
            why_it_matters="Without command guardrails, unsafe or off-policy shell commands can execute during lane/gate runtime.",
            recommendation="Enforce allow/deny regex policy for lane/gate commands and validate with regression scenarios.",
        ),
        TestCase(
            scenario="Human-approval guardrail mode is available for operator-led runs",
            severity="medium",
            patterns=[
                r"human-approval",
                r"approval_non_interactive_decision",
                r"approval_auto_allow_patterns|operator approval",
            ],
            required_files=[
                "scripts/runtime/command_guardrails.py",
                "scripts/guardrails_regression_test.py",
            ],
            why_it_matters="Interactive operators need approve/deny flow for context-safe commands that static denylist would block.",
            recommendation="Support human-approval mode with non-interactive fallback and keep regression scenarios for allow/deny behavior.",
        ),
        TestCase(
            scenario="Path-aware and role/environment guardrail overrides are supported",
            severity="medium",
            patterns=[
                r"approval_safe_path|safe-delete|safe path",
                r"role_policies|environment_policies|guardrail environment",
            ],
            required_files=[
                "scripts/runtime/command_guardrails.py",
                "scripts/bootstrap_runbook.py",
                "scripts/guardrails_regression_test.py",
            ],
            why_it_matters="Rigid global denylist causes false positives unless path- and context-aware override logic is available.",
            recommendation="Add safe-path auto approvals and role/environment policy overrides, then keep regression tests for precedence.",
        ),
        TestCase(
            scenario="AST-based code intelligence evaluates touched Python/TypeScript files",
            severity="high",
            patterns=[
                r"code intelligence|code-intel|code_intelligence",
                r"symbol|import|high-risk",
                r"ast|typescript",
            ],
            required_files=[
                "scripts/runtime/code_intelligence.py",
                "scripts/runtime/worker_adapters.py",
                "scripts/code_intelligence_regression_test.py",
            ],
            why_it_matters="Command regex safety cannot detect large-blast-radius code edits without file-level impact analysis.",
            recommendation="Analyze touched code files with Python AST/TypeScript parsing and enforce/audit via regression scenarios.",
        ),
        TestCase(
            scenario="Runbook lint requires guardrail policy and profile templates",
            severity="high",
            patterns=[
                r"runbook lint|runtime_runbook_lint\.py",
                r"guardrail profile|dev|ci|prod",
                r"os template|windows|linux|darwin",
            ],
            required_files=[
                "scripts/runtime/runbook_lint.py",
                "scripts/runtime_runbook_lint.py",
                "scripts/runbook_lint_regression_test.py",
                "scripts/bootstrap_runbook.py",
            ],
            why_it_matters="Without a mandatory lint rule, runbooks can bypass safety policy and execute unguarded commands.",
            recommendation="Enforce limits.command_guardrails in runbook lint, include profile/os template support, and keep regression coverage.",
        ),
        TestCase(
            scenario="Daemon enqueue and hybrid bridge are lint-gated",
            severity="high",
            patterns=[
                r"runtime_daemon_cli\.py",
                r"enqueue",
                r"skip-runbook-lint|runbook lint",
                r"hybrid_pipeline\.py",
                r"skip-runbook-lint|runbook lint failed after normalization",
            ],
            required_files=[
                "scripts/runtime/daemon.py",
                "scripts/runtime_daemon_cli.py",
                "scripts/hybrid_pipeline.py",
                "scripts/entrypoint_lint_regression_test.py",
            ],
            why_it_matters="If daemon enqueue or hybrid conversion bypass lint, unsafe runbooks can still reach runtime execution.",
            recommendation="Gate daemon enqueue and hybrid normalize paths with strict runbook lint and keep an entrypoint regression suite.",
        ),
        TestCase(
            scenario="Daemon stale-job recovery present",
            severity="high",
            patterns=[
                r"recover --stale-sec",
            ],
            required_files=[
                "scripts/runtime/daemon.py",
                "scripts/runtime_daemon_cli.py",
            ],
            why_it_matters="Without stale processing recovery, daemon crashes can strand jobs forever.",
            recommendation="Implement stale processing requeue/fail policy with retry caps.",
        ),
        TestCase(
            scenario="Non-core maintenance utilities present",
            severity="low",
            patterns=[
                r"Non-core utilities",
                r"runtime_maintenance\.py",
                r"prune-runs",
                r"compact-events",
            ],
            required_files=[
                "scripts/runtime_maintenance.py",
            ],
            why_it_matters="Operational hygiene gets harder as event/state artifacts accumulate.",
            recommendation="Add maintenance utilities for pruning, compaction, and cleanup.",
        ),
        TestCase(
            scenario="Non-core report generation present",
            severity="low",
            patterns=[
                r"runtime_report\.py",
                r"--format md",
            ],
            required_files=[
                "scripts/runtime_report.py",
            ],
            why_it_matters="Periodic summaries help operators inspect runtime health without manual log parsing.",
            recommendation="Add JSON/Markdown reporting utilities for run history.",
        ),
        TestCase(
            scenario="Fixed runtime benchmark scorecard is available",
            severity="medium",
            patterns=[
                r"runtime_benchmark\.py",
                r"benchmark",
                r"score",
                r"baseline",
            ],
            required_files=[
                "scripts/runtime_benchmark.py",
            ],
            why_it_matters="Without a fixed benchmark, version-to-version progress claims are hard to validate objectively.",
            recommendation="Provide a deterministic benchmark suite with weighted scoring and baseline deltas.",
        ),
        TestCase(
            scenario="Specialist role registry + role presets are executable",
            severity="high",
            patterns=[
                r"Paper-backed role presets",
                r"agent_runtime_cli\.py",
                r"agents",
                r"Specialist",
            ],
            required_files=[
                "scripts/agent_runtime_cli.py",
                "scripts/runtime/specialist_registry.py",
            ],
            why_it_matters="Role diversity must be queryable at runtime, not only described in docs.",
            recommendation="Expose specialist list/get commands and map lane owner roles to registry ids.",
        ),
        TestCase(
            scenario="Team communication message bus is runtime-wired",
            severity="high",
            patterns=[
                r"Message bus envelope",
                r"runtime_dashboard\.py",
                r"messages_total",
            ],
            required_files=[
                "scripts/runtime/message_bus.py",
                "scripts/runtime/orchestrator.py",
                "scripts/runtime_dashboard.py",
            ],
            why_it_matters="Communication claims are weak if no live message log exists during orchestration.",
            recommendation="Emit lane/checkpoint/finalize envelopes to the message bus and surface counts in dashboard/report.",
        ),
        TestCase(
            scenario="Consensus protocol scaffold is available",
            severity="medium",
            patterns=[
                r"consensus",
                r"propose",
                r"critique",
                r"vote",
                r"finalize",
            ],
            required_files=[
                "scripts/runtime/consensus_engine.py",
                "scripts/agent_runtime_cli.py",
            ],
            why_it_matters="Structured agreement rounds reduce ad-hoc conflict handling in multi-role plans.",
            recommendation="Provide create/propose/critique/vote/finalize round commands with persisted artifacts.",
        ),
        TestCase(
            scenario="Capability matrix against OMC is documented",
            severity="medium",
            patterns=[
                r"Capability Matrix",
                r"PE v2",
                r"OMC",
                r"독립 런타임|Independent runtime",
                r"잡 큐|Queue",
                r"게이트 엔진|gate",
            ],
            why_it_matters="Users need an explicit dimension-by-dimension boundary for realistic adoption decisions.",
            recommendation="Maintain a dated capability matrix with implemented/partial limits and next upgrades.",
        ),
        TestCase(
            scenario="Process-worker adapter for separated subprocess runtime",
            severity="medium",
            patterns=[
                r"process-worker",
                r"worker-cmd-template|command_template",
            ],
            required_files=[
                "scripts/runtime/worker_adapters.py",
                "scripts/bootstrap_team_manifest.py",
            ],
            why_it_matters="Bridging toward real multi-agent behavior needs lane-level separated process execution.",
            recommendation="Keep process-worker + manifest command templates wired into orchestrator runtime context.",
        ),
        TestCase(
            scenario="AI worker restricted to codex/gemini with unavailable-skip policy",
            severity="high",
            patterns=[
                r"ai-worker",
                r"codex",
                r"gemini",
                r"unavailable-skip|not logged in|GEMINI_API_KEY",
            ],
            required_files=[
                "scripts/runtime/worker_adapters.py",
                "scripts/bootstrap_team_manifest.py",
            ],
            why_it_matters="Operational stability requires explicit behavior when AI CLI auth is missing.",
            recommendation="Allow only codex/gemini engines and skip unavailable workers without failing whole run.",
        ),
        TestCase(
            scenario="AI worker E2E regression harness present",
            severity="medium",
            patterns=[
                r"ai-worker",
                r"codex",
                r"gemini",
                r"skip",
            ],
            required_files=[
                "scripts/ai_worker_regression_test.py",
            ],
            why_it_matters="Changes in CLI auth/runtime behavior can silently regress without scenario coverage.",
            recommendation="Keep codex-only/gemini-explicit/gemini-missing-key end-to-end scenarios automated.",
        ),
        TestCase(
            scenario="Hybrid bridge for frontstage planner to PE runtime",
            severity="high",
            patterns=[
                r"Hybrid bridge",
                r"hybrid_pipeline\.py",
                r"frontstage",
                r"planner-cmd",
                r"frontstage-plan",
            ],
            required_files=[
                "scripts/hybrid_pipeline.py",
            ],
            why_it_matters="Teams need a concrete migration path that keeps frontstage planning while using PE execution operations.",
            recommendation="Keep one-command bridge that normalizes frontstage plan artifacts into PE runbook/manifest and executes runtime.",
        ),
        TestCase(
            scenario="Frontstage multi-agent codex consensus planner is available",
            severity="high",
            patterns=[
                r"frontstage",
                r"codex",
                r"teams",
                r"consensus",
                r"critique-revise|debate-mode",
                r"agent-runtime|persistent",
            ],
            required_files=[
                "scripts/frontstage_codex_teams.py",
                "scripts/frontstage_codex_teams_regression_test.py",
                "scripts/frontstage_role_worker.py",
            ],
            why_it_matters="Without a real frontstage generator, hybrid mode depends on external tools and cannot run end-to-end in one stack.",
            recommendation="Provide a parallel multi-role frontstage planner that emits frontstage-plan JSON for hybrid bridge.",
        ),
        TestCase(
            scenario="IPC + file double logging control plane with mid-run replan",
            severity="high",
            patterns=[
                r"control",
                r"IPC",
                r"replan",
                r"double logging|jsonl",
            ],
            required_files=[
                "scripts/runtime/control_plane.py",
                "scripts/runtime_control_cli.py",
                "scripts/ipc_control_regression_test.py",
            ],
            why_it_matters="Real-time consensus adjustments need live IPC while preserving replayable file logs.",
            recommendation="Keep control-plane IPC server + JSONL recording and validate with mid-run replan regression test.",
        ),
        TestCase(
            scenario="Mid-run consensus reconfigure via IPC is supported",
            severity="high",
            patterns=[
                r"consensus",
                r"control",
                r"IPC",
                r"reconfigure|consensus-patch|consensus_reconfigure",
            ],
            required_files=[
                "scripts/runtime/orchestrator.py",
                "scripts/runtime_control_cli.py",
                "scripts/consensus_reconfigure_regression_test.py",
            ],
            why_it_matters="Consensus policy often needs in-flight adjustment; without this, runs fail even when policy-only correction is enough.",
            recommendation="Support consensus policy patch messages during run and validate with IPC E2E regression.",
        ),
        TestCase(
            scenario="Delegate-worker E2E regression harness present",
            severity="medium",
            patterns=[
                r"delegate-worker",
                r"runtime_delegate_cli\.py",
                r"delegate",
            ],
            required_files=[
                "scripts/runtime/delegate_bus.py",
                "scripts/runtime/delegate_worker.py",
                "scripts/runtime_delegate_cli.py",
                "scripts/delegate_worker_regression_test.py",
            ],
            why_it_matters="Separated worker processes need an end-to-end health check to prevent silent queue regressions.",
            recommendation="Keep delegate adapter + queue bus + CLI + E2E regression in one validated path.",
        ),
        TestCase(
            scenario="Advanced consensus policy controls and regression present",
            severity="medium",
            patterns=[
                r"consensus",
                r"evaluate",
                r"reject-threshold",
                r"required-roles",
                r"single-winner",
            ],
            required_files=[
                "scripts/runtime/consensus_engine.py",
                "scripts/agent_runtime_cli.py",
                "scripts/consensus_regression_test.py",
            ],
            why_it_matters="Consensus quality drops without explicit policy controls and regression coverage for veto/quorum/role rules.",
            recommendation="Expose weighted policy knobs in CLI and keep regression scenarios for veto/single-winner/required-role behavior.",
        ),
        TestCase(
            scenario="Plan-search candidate scoring and selection wired",
            severity="high",
            patterns=[
                r"Use plan search on hard tasks",
                r"candidate plans",
                r"fallback plan",
            ],
            required_files=[
                "scripts/runtime/plan_search.py",
                "scripts/runtime_plan_cli.py",
                "scripts/plan_search_regression_test.py",
                "scripts/runtime/orchestrator.py",
            ],
            why_it_matters="Without candidate scoring, replans stay ad-hoc and brittle under uncertainty.",
            recommendation="Keep candidate scoring + winner selection in orchestrator and validate with E2E replan regression.",
        ),
        TestCase(
            scenario="Checkpoint consensus gate is runtime-enforced",
            severity="high",
            patterns=[
                r"consensus",
                r"checkpoint",
                r"consensus evaluate",
            ],
            required_files=[
                "scripts/runtime/gate_engine.py",
                "scripts/runtime/consensus_engine.py",
                "scripts/consensus_checkpoint_regression_test.py",
            ],
            why_it_matters="Consensus remains documentation-only unless checkpoint pass/fail depends on real round evaluation.",
            recommendation="Evaluate consensus rounds at checkpoint time and fail run on decision mismatch.",
        ),
        TestCase(
            scenario="Checkpoint consensus auto-create pipeline is available",
            severity="high",
            patterns=[
                r"consensus",
                r"auto-create|auto create",
                r"checkpoint",
            ],
            required_files=[
                "scripts/runtime/gate_engine.py",
                "scripts/bootstrap_runbook.py",
                "scripts/consensus_autocreate_regression_test.py",
            ],
            why_it_matters="If round ids are missing, consensus gating stalls unless runtime can auto-create proposal/vote artifacts.",
            recommendation="Support auto-create round+proposal+synthetic votes in checkpoint consensus gate and cover with regression test.",
        ),
        TestCase(
            scenario="Consensus synthetic-vote templates are file-backed",
            severity="medium",
            patterns=[
                r"synthetic",
                r"template",
                r"consensus",
            ],
            required_files=[
                "scripts/runtime/consensus_templates.py",
                "scripts/bootstrap_consensus_template.py",
                "scripts/consensus_template_regression_test.py",
            ],
            why_it_matters="Reusable role vote patterns should be versioned files, not duplicated inline checkpoint payloads.",
            recommendation="Load synthetic votes from template files and validate pass/fail behavior with regression tests.",
        ),
    ]


def check_case(text: str, case: TestCase, skill_dir: Path) -> bool:
    if not has_all(text, case.patterns):
        return False
    for rel_path in case.required_files:
        if not (skill_dir / rel_path).exists():
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scenario stress tests for plan-executor skill.")
    parser.add_argument(
        "--skill-md",
        default="C:/Users/JSC/.codex/skills/plan-executor/SKILL.md",
        help="Path to SKILL.md",
    )
    args = parser.parse_args()

    skill_path = Path(args.skill_md)
    if not skill_path.exists():
        print(f"[ERROR] Missing file: {skill_path}")
        return 2

    skill_dir = skill_path.parent
    text = skill_path.read_text(encoding="utf-8")
    cases = build_cases()

    passed = 0
    failed = []
    print("Plan Executor Scenario Stress Test")
    print("=" * 44)
    for i, case in enumerate(cases, start=1):
        ok = check_case(text, case, skill_dir)
        status = "PASS" if ok else "FAIL"
        print(f"{i:02d}. [{status}] ({case.severity}) {case.scenario}")
        if ok:
            passed += 1
        else:
            failed.append(case)

    total = len(cases)
    score = int(round((passed / total) * 100))
    print("-" * 44)
    print(f"RESULT: {passed}/{total} passed  |  score={score}/100")

    if failed:
        print("\nTop gaps to fix:")
        severity_order = {"high": 0, "medium": 1, "low": 2}
        failed_sorted = sorted(failed, key=lambda c: severity_order.get(c.severity, 3))
        for case in failed_sorted:
            print(f"- [{case.severity}] {case.scenario}")
            print(f"  Why: {case.why_it_matters}")
            print(f"  Fix: {case.recommendation}")
    else:
        print("\nNo major gaps detected by this harness.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
