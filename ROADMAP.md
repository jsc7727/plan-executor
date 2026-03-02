# plan-executor Implementation Roadmap

This roadmap is for `https://github.com/jsc7727/plan-executor`.

## 0. Goal

- Goal: stabilize `plan-executor` as an independent multi-agent orchestration runtime. Primary engine is Codex; shell serves as fallback.
- Principles:
  - Keep PE strengths first: queue, gate, resume, dashboard.
  - Multi-agent execution is PE-native, not a bridge to external systems.
  - Codex-first: optimize the default path for Codex CLI, support others as fallback.
  - Prefer small, verifiable changes over large refactors.

## 1. Current Status

Summary of what exists as of 2026-03-02:

- Entrypoints: `runtime_cli.py`, `runtime_daemon_cli.py`, `runtime_control_cli.py` (all working).
- Runtime modules: orchestrator, daemon, gate_engine, event_store, message_bus, control_plane, delegate_bus, delegate_worker, worker_adapters, runbook_lint, command_guardrails, specialist_registry, consensus_engine, consensus_templates, plan_search, code_intelligence.
- Multi-agent: `frontstage_codex_teams.py` (ThreadPoolExecutor parallel agents with propose/critique/revise consensus), `frontstage_role_worker.py` (persistent worker IPC), `hybrid_pipeline.py` (frontstage plan to runbook bridge).
- Engine support: Codex (`codex exec`, primary), shell (subprocess, fallback).
- Regression tests: guardrails (7/7), runbook_lint (3/3), consensus (2/2), plan_search (2/2) — all passing.
- Sample runbooks: speed, hardening, swarm-manifest.
- Missing: README/install docs, CI pipeline, E2E automation.

## 2. Milestones

### Phase 1: Baseline (MVP operations ready)

- Status: **~85% complete**.
- Tasks:
  - ~~Normalize entrypoints: `runtime_cli.py`, `runtime_daemon_cli.py`, `runtime_control_cli.py`.~~ Done.
  - Add minimum docs: README with install, quick start, runbook examples.
  - ~~Standardize core regression command set.~~ Done (4 test suites, all passing).
  - ~~Validate artifact directory conventions (`.plan-executor/*`).~~ Done.
- Remaining:
  - Write `README.md` with install steps, Python version requirement, and one-command quick start.
  - Add inline `--help` examples for each CLI entrypoint.
- Done criteria:
  - One baseline runbook runs successfully on a clean clone.
  - `state/events/messages` are generated and visible in dashboard.

### Phase 2: Reliability and guardrails

- Status: **~70% complete**.
- Tasks:
  - Promote `runbook_lint` and `command_guardrails` to CI gates.
  - ~~Validate failure recovery paths (`resume`, `recover`, `abort`).~~ Implemented in `runtime_cli.py`.
  - ~~Connect `runtime_report` and `runtime_benchmark` to release checklist.~~ Scripts exist.
- Remaining:
  - ~~Add GitHub Actions CI workflow running all 7 regression test suites on PR.~~ Done.
  - Add resume/recover regression test (currently untested in CI).
- Done criteria:
  - Risky command blocking regressions pass in CI.
  - Queue recovery scenario passes at least once in CI.
  - Invalid runbook is blocked before queue entry.

### Phase 3: Multi-Agent Engine (Codex-first parallel orchestration)

- Status: **~75% complete** (engine flag, hybrid failure handling, fallback chain + repair-engine split implemented; real Codex E2E untested).
- What exists:
  - `frontstage_codex_teams.py`: parallel role agents with consensus (propose/critique/revise).
  - `delegate_worker.py`: Codex engine detection and command wrapping.
  - `hybrid_pipeline.py`: frontstage plan to PE runbook conversion.
  - `frontstage_role_worker.py`: persistent worker processes with JSON IPC.
  - `worker_adapters.py`: execution-engine fallback is independent from repair engine; logic-failure repair is routed via Codex to avoid natural-language repair prompt execution on shell fallback.
- Tasks:
  - Wire frontstage → hybrid_pipeline → orchestrator as one-command E2E flow.
  - ~~Add engine fallback policy: Codex fail → shell, configurable per runbook.~~ Done. Includes runtime fallback with template synchronization.
  - ~~Add `--engine` flag to `runtime_cli.py start` for agent engine selection.~~ Done.
  - Expand `frontstage_codex_teams_regression_test.py` to cover multi-engine scenarios with mocks.
  - ~~Add hybrid failure handling: PE handles infrastructure/guardrail failures deterministically, logic failures (test fail, build error, wrong output) are routed back to Codex with error context for AI-judged repair, bounded by `max_replan` to prevent infinite loops.~~ Done.
- Done criteria:
  - `python scripts/frontstage_codex_teams.py --objective "..." --agent-cmd-template "codex exec ..."` runs end-to-end with real Codex.
  - Engine fallback triggers automatically on infrastructure failure without full run abort.
  - Logic failure triggers Codex repair loop, capped at max_replan attempts.
  - Codex engine completes frontstage consensus without error.

### Phase 4: Specialist and consensus upgrades

- Status: **~30% complete** (foundations exist).
- What exists:
  - `specialist_registry.py`: role registry.
  - `consensus_engine.py` + `consensus_templates.py`: veto/quorum, multi-round consensus.
  - Consensus regression test with veto scenario passing.
- Tasks:
  - Define role SOPs per specialist (planner, architect, frontend, backend, qa, security, devops).
  - Map PE role SOPs to Codex `[agents]` config: generate `.codex/config.toml` with role-specific model, instructions, sandbox policy per specialist.
  - Add consensus template presets: strict (high quorum), fast (low threshold), balanced.
  - Document IPC mid-run replan operations via `runtime_control_cli.py`.
  - Add consensus reconfigure regression (change policy mid-run).
- Done criteria:
  - Checkpoint consensus failure and reconfigure regressions pass.
  - Role mismatch and conflict policies are automatically enforced.
  - PE role SOPs and Codex `[agents]` config are generated from a single source of truth.

### Phase 5: Release hardening

- Status: **~15% complete**.
- What exists:
  - 3 sample runbooks (speed, hardening, swarm-manifest).
  - `runtime_report.py`, `runtime_benchmark.py`, `runtime_maintenance.py`.
- Tasks:
  - Define versioning policy (tag + changelog).
  - Add balanced-profile sample runbook.
  - Add multi-agent sample runbook (frontstage + execution E2E).
  - Write operations docs: incident response, maintenance, benchmark reading.
  - Write contributor guide.
- Done criteria:
  - New user can run one E2E flow within 30 minutes.
  - Release checklist is fully green.

## 3. Branch and PR strategy

- Branch model:
  - `main`: stable release branch.
  - `feat/*`: feature development.
  - `hardening/*`: reliability and regression work.
- PR rules:
  - One primary change per PR.
  - Include purpose, commands run, and key output summary.
  - If regression tests are changed, explain why.

## 4. Validation matrix

- Core validation:
  - `python scripts/runtime_cli.py ... start`
  - `python scripts/runtime_daemon_cli.py ... enqueue/run-once`
  - `python scripts/runtime_dashboard.py ...`
- Safety validation:
  - `python scripts/guardrails_regression_test.py --project-root <project-root>`
  - `python scripts/runbook_lint_regression_test.py --project-root <project-root>`
- Orchestration validation:
  - `python scripts/plan_search_regression_test.py --project-root <project-root>`
  - `python scripts/consensus_regression_test.py --project-root <project-root>`
- Multi-agent validation:
  - `python scripts/frontstage_codex_teams_regression_test.py --project-root <project-root>`
  - `python scripts/delegate_worker_regression_test.py --project-root <project-root>`
  - `python scripts/ai_worker_regression_test.py --project-root <project-root>`

## 5. Action plan

Priority order (each can be a single PR):

1. ~~`README.md` — install, quick start, architecture diagram. Closes Phase 1.~~ Done.
2. ~~GitHub Actions CI — run all 7 regression test suites on PR. Closes Phase 2.~~ Done.
3. One-command E2E — `runtime_cli.py start --runbook ... --engine codex` runs frontstage + execution with real Codex. Phase 3 core.
4. ~~Hybrid failure handling — PE deterministic for infra/guardrail, Codex repair loop for logic failures, bounded by max_replan. Phase 3 core.~~ Done.
5. Multi-engine mock tests — expand frontstage regression to cover Codex/shell engine switching with mocks. Phase 3 test.
6. ~~Engine fallback policy — configurable per-runbook fallback chain (Codex → shell), with runtime template synchronization on engine switch. Phase 3 hardening.~~ Done.
7. Role SOPs + consensus presets + Codex `[agents]` config generation — Phase 4.
8. Release docs + versioning — Phase 5.

## 6. Success metrics

- Operations stability:
  - runbook lint pre-block rate: 100%.
  - recovery success rate: >= 95%.
- Execution quality:
  - verification pass rate: >= 0.85.
- Multi-agent quality:
  - Codex frontstage consensus completion rate: >= 80%.
  - engine fallback success rate (Codex → shell): >= 95%.

## 7. Out of scope (current stage)

- Deeply coupling PE runtime to any single external orchestrator (OMC, LangGraph, etc).
- Full module rewrite or broad architecture reset.
- Custom model hosting or fine-tuning within PE.
- Using Claude CLI as automated subprocess worker (ToS risk).
