---
name: plan-executor
description: Build a concrete, stepwise execution plan and carry it through to completion. Use when work has multiple steps, dependencies, or verification needs; when the user asks to "plan first then keep executing"; when teams-like role splitting is requested; or when autonomous end-to-end delivery is expected instead of plan-only output.
---

# Plan Executor

## Overview

Convert broad requests into a finite plan, then execute continuously until done.
Default to action after planning; stop only for true blockers.
For research-backed rationale and citations, read `references/orchestrator-papers.md` on demand.

## Task-Type Branching

### Choose a primary path first

- Classify the task as `code`, `document`, or `research` before writing detailed steps.
- Choose one primary path for the first execution cycle.
- Split into phases if the request spans multiple paths (for example: research -> code -> document).

### Path: code

- Inspect the local codebase and reproduce the issue or confirm requirements.
- Create a minimal implementation plan with explicit file targets.
- Implement the smallest complete patch first.
- Run targeted validation (tests, typecheck, lint, or build) before broad validation.
- Report exact changed files, verification commands, and any residual risk.

### Path: document

- Confirm audience, format, tone, and required structure.
- Build a short outline before drafting full content.
- Produce the deliverable, then run a quality pass for consistency and missing sections.
- Verify factual claims against provided context or cited sources when needed.
- Return the final text in the requested format with a concise change summary.

### Path: research

- Define the decision question, evaluation criteria, and constraints.
- Collect high-signal sources and prefer primary documentation.
- Compare options with explicit tradeoffs, not feature lists only.
- Conclude with a recommendation, confidence level, and dated sources.
- Translate findings into actionable next steps for implementation.

## Workflow

### 1. Frame the objective

- Extract target outcome, constraints, and done criteria.
- State assumptions briefly when inputs are missing.
- Define what must be verified at the end.

### 2. Create an execution plan

- Build 3-7 concrete steps with observable outputs.
- Order steps by dependency and risk.
- Default to one `in_progress` step.
- Switch to parallel lanes only when steps are independent.

## Parallel Execution Mode

### When to enable

- Enable only if branches do not share mutable state or blocking dependencies.
- Keep sequential mode for risky migrations, cross-cutting refactors, or unclear requirements.

### How to plan lanes

- Split work into 2-4 independent lanes with explicit outputs.
- Define each lane's files or artifacts to avoid overlap.
- Add merge checkpoints where outputs are integrated and verified.

### DAG format example

- Express lane dependencies as a DAG before execution.
- Paper basis: `LLMCompiler`, `Graph of Thoughts` in `references/orchestrator-papers.md`.

```json
{
  "nodes": [
    {"id": "lane-a", "depends_on": []},
    {"id": "lane-b", "depends_on": ["lane-a"]},
    {"id": "lane-c", "depends_on": ["lane-a"]}
  ]
}
```

### How to run

- Allow multiple steps as `in_progress` only while parallel mode is active.
- Execute independent commands/tasks concurrently when tooling supports parallel runs.
- Keep progress logs per lane, then publish a merged status summary.

### Synchronization rules

- Rejoin at each checkpoint before continuing to dependent work.
- Resolve conflicts immediately if two lanes touch the same target.
- If interference appears, pause parallel mode and return to sequential execution.
- Define checkpoint acceptance criteria before starting each lane cycle.
- Paper basis: `CRITIC`, `SWE-agent`, `Large Language Models Cannot Self-Correct Reasoning Yet?`.
- Example gate criteria:
  - code: tests pass + lint clean + required artifact updated
  - document: required sections complete + factual claims sourced
  - research: recommendation present + dated sources attached

## Multi-Agent Emulation Mode

### Capability boundary

- Treat this as orchestrated role emulation, not truly separate model runtimes.
- If the user requests "separate agents," provide this mode as the closest supported behavior.

### Role model

- `orchestrator`: Own global plan, lane assignment, and final integration.
- `agent-1..N`: Execute assigned lane tasks and produce lane-local outputs.
- `integrator`: Merge outputs at checkpoints and run cross-lane verification.

### Paper-backed role presets

Use one of these presets before assigning lanes.

#### Preset A: `metagpt-swe-line` (software delivery)

- Paper basis: `MetaGPT`.
- Roles:
  - `product-manager`: Define PRD and requirement pool.
  - `architect`: Define file list, data structures, interface specs.
  - `project-manager`: Break tasks and assign execution order.
  - `engineer`: Implement code and fix runtime issues.
  - `qa-engineer`: Build test cases and enforce quality gates.
- Best for: end-to-end feature delivery with structured handoffs.

#### Preset B: `chatdev-waterfall` (design -> coding -> testing -> docs)

- Paper basis: `ChatDev`.
- Roles:
  - `ceo`, `cpo`, `cto`: Product direction, modality, language decisions.
  - `programmer`: Implement source code.
  - `art-designer`: Produce UI assets and visual direction.
  - `reviewer`, `tester`: Static review + dynamic testing feedback loops.
- Best for: app generation with strong phase separation.

#### Preset C: `autogen-duo-plus` (execution + tool/human proxy)

- Paper basis: `AutoGen`.
- Roles:
  - `assistant-agent`: LLM planner/solver that proposes code and steps.
  - `user-proxy-agent`: Execute tools/code, collect feedback, optionally human-in-the-loop.
  - `group-chat-manager` (optional): Route turns in multi-party dynamic chat.
- Best for: rapid iterative execution with strong tool feedback.

#### Preset D: `product-web-app` (practical team mapping)

- Paper basis: combine `MetaGPT` role specialization + `ChatDev` design/coding/testing split.
- Roles:
  - `planner`: Scope, priority, done criteria (`product-manager`/`cpo` analogue).
  - `designer`: IA, UI flow, visual assets (`architect` + `art-designer` analogue).
  - `frontend`: UI implementation and FE tests (`engineer/programmer` analogue).
  - `backend`: API/data model and BE tests (`engineer/programmer` analogue).
  - `qa`: Review + system testing (`reviewer` + `tester` + `qa-engineer` analogue).
- Best for: real product work where user asked for PM/Designer/FE/BE style roles.

#### Preset selection rules

- Start with `Preset D` for typical web product tasks.
- Use `Preset A` when strict artifact contracts are required.
- Use `Preset B` when explicit waterfall phases are preferred.
- Use `Preset C` when tool execution/human proxy loops dominate.

### Activation criteria

- Enable when the task has at least 2 independent tracks with clear interfaces.
- Disable when work is tightly coupled or merge risk is high.

### Operating protocol

- Create an agent card per lane with:
  - scope
  - input artifacts
  - output contract
  - done criteria
- Run lanes in parallel cycles and log updates per agent card.
- Use checkpoint gates for integration; do not let lanes bypass gates.

### Agent card schema template

Use this fixed agent card template for consistent handoff:
- Paper basis: `MetaGPT`, `AutoGen`, `ChatDev`.

```yaml
agent_card:
  id: lane-a
  scope: "Implement API validation changes"
  input_artifacts:
    - "src/api/schema.ts"
    - "tests/api/validation.test.ts"
  output_contract:
    files_changed:
      - "src/api/schema.ts"
      - "tests/api/validation.test.ts"
    acceptance:
      - "targeted tests pass"
      - "no lint errors in changed files"
  done_criteria:
    - "checkpoint accepted by integrator"
```

### Conflict control

- Assign file ownership per lane when possible.
- If two lanes need the same target, route changes through `integrator`.
- On repeated collisions, collapse to sequential execution and replan.

### Conflict decision matrix

- Use this tie-break precedence rule:
- Paper basis: `MetaGPT`, `AutoGen` role/interface coordination.
  - owner lane wins for lane-owned files
  - integrator decides for shared files
  - if still ambiguous, choose lower-risk patch and schedule follow-up

### 2.5. Orchestrator Upgrades (Paper-Backed)

#### Use plan search on hard tasks

- Generate 2-3 candidate plans when uncertainty is high.
- Score candidates by feasibility, dependency risk, and verification coverage.
- Execute the top plan and keep one fallback plan for fast recovery.

#### Require external feedback for correction

- Treat pure self-critique as advisory only.
- Require at least one external signal before accepting a correction:
  - test result
  - compiler/linter output
  - tool execution result
  - retrieval-backed evidence

#### Enforce structured lane contracts

- Define every lane with a strict contract:
  - objective
  - inputs
  - output schema
  - constraints
  - verification method
- Reject lane outputs that violate the declared contract.

#### Schedule with a dependency DAG

- Build a dependency graph before parallel execution.
- Parallelize only nodes with no unresolved dependencies.
- Recompute the graph after each checkpoint when scope changes.

#### Add budget and stall control

- Set per-lane limits for time, tool calls, and retries.
- Track stall rounds; trigger replanning after repeated no-progress cycles.
- Downshift from parallel to sequential mode when coordination overhead exceeds gains.

#### Keep episodic memory and SOP updates

- Log recurring failures, successful fixes, and reusable prompts/patterns.
- Feed these memory notes into the next planning cycle.
- Promote repeated wins into stable SOP snippets inside this skill.

### 3. Start executing immediately

- Execute step 1 right after publishing the plan.
- Emit short progress updates after meaningful actions.
- Finish each step with evidence (command result, diff summary, or artifact).

### 4. Keep a rolling plan

- Mark completed steps as `completed`.
- Move the next step to `in_progress`.
- Revise downstream steps when discoveries change scope.
- Keep the plan current instead of writing a second disconnected plan.

### 5. Replan on blockers

- Retry once after fixing obvious local issues.
- Replan when assumptions break, requirements change, or repeated failure occurs.
- Set a max replan limit: `max_replan=3`.
- Paper basis: `Language Agent Tree Search`, `Reflexion` (bounded retry + reflection loops).
- If max replan limit is reached, escalate with:
  - best-known partial result
  - unresolved blockers
  - recommended next human decision
- Publish a compact replan with:
  - what changed
  - updated steps
  - immediate next action

### 6. Verify and close

- Run tests/checks or best-available validation.
- Confirm done criteria explicitly.
- Report changed files/artifacts and residual risks.

## Guardrails

- Continue autonomously unless blocked by missing credentials, destructive-risk approval, or irreducible ambiguity.
- Prefer the smallest complete change that satisfies the task.
- Do not stop at plan-only output unless the user explicitly asks for planning only.

## Tooling Pattern

- Use a plan tracker tool when available and keep statuses synchronized.
- If no plan tracker exists, maintain a concise checklist in the response.
- Keep exactly one active execution step at a time unless parallel mode is active.
- In parallel mode, keep one active step per lane and one merged checkpoint status.
- Track minimal orchestrator metrics: `stall_rounds`, `replan_count`, `verification_pass_rate`, `merge_conflicts`.

## Operations Layer (OMC-Inspired, Skill-Native)

### Execution profiles

Select one profile before execution.

- `speed`:
  - lanes: up to 4
  - checkpoint strictness: minimal
  - validation: targeted checks only
  - use when: rapid prototyping and low blast radius
- `balanced`:
  - lanes: 2-3
  - checkpoint strictness: standard
  - validation: targeted + one broad check
  - use when: default production work
- `hardening`:
  - lanes: 1-2
  - checkpoint strictness: strict
  - validation: targeted + broad + regression bundle
  - use when: risky refactors, infra, security-sensitive changes

### Event hooks

Emit these hook events in every non-trivial run:

- `preflight`: objective, constraints, selected preset/profile, risk summary
- `lane_start`: lane id, owner role, contract
- `lane_done`: changed artifacts, local verification result
- `checkpoint`: gate decision (`pass`/`fail`) and evidence
- `post_merge`: merged diff summary, unresolved conflicts
- `finalize`: done-criteria decision, residual risks, next actions

### Escalation matrix

Escalate to user with a decision request when any trigger fires:

- `replan_count >= max_replan`
- `stall_rounds >= 2` in the same lane
- `merge_conflicts >= 2` at one checkpoint
- `verification_pass_rate < 0.7` after checkpoint retries

Escalation payload must include:

- current best partial result
- blockers and failed evidence
- 1-2 concrete decision options

### Runbook bootstrap

- Generate a runbook artifact before complex runs (2+ lanes).
- Use `scripts/bootstrap_runbook.py` to create a JSON runbook with:
  - preset and execution profile
  - lane DAG
  - agent cards
  - checkpoint gates
- Optionally attach `consensus_gate` to checkpoint definitions for decision-based gating.
- If `consensus_gate.round_id` is missing, enable `consensus_gate.auto_create_round` to auto-create round/proposal/votes at checkpoint time.
- Use `consensus_gate.synthetic_votes_template` to load reusable role vote patterns from file templates.
- Set `meta.max_parallel_workers` for true concurrent scheduling.
- Use checkpoint `gate_commands` for executable pass/fail gates.
- Configure `limits.ai_worker_skip_warn_streak` / `limits.ai_worker_skip_fail_streak` for unavailable AI worker streak policy.
- Configure `limits.command_guardrails` with profile presets (`dev`/`ci`/`prod`) and OS template (`auto`/`windows`/`linux`/`darwin`/`common`).
- Optionally set guardrail environment/role overrides:
  - `--guardrail-environment auto|dev|ci|prod`
  - `--guardrail-role-policy planner:human-approval` (repeatable)
- Default output path: `<project-root>/.plan-executor/runbooks/`.
- Keep the runbook updated when replanning.

Runbook bootstrap with consensus auto-create:

```bash
python C:/Users/JSC/.codex/skills/plan-executor/scripts/bootstrap_runbook.py \
  --project-root <project-root> \
  --preset product-web-app \
  --profile balanced \
  --mode parallel \
  --lanes 3 \
  --consensus-auto-create-round \
  --consensus-participants planner,architect,qa \
  --consensus-auto-vote-mode approve-all
```

### Artifact separation

Keep skill files and run artifacts separate:

- Skill definition stays in `C:/Users/<user>/.codex/skills/plan-executor/`.
- Project execution artifacts go to `<project-root>/.plan-executor/`.
- Recommended structure:
  - `<project-root>/.plan-executor/runbooks/`
  - `<project-root>/.plan-executor/team-manifests/`
  - `<project-root>/.plan-executor/events/`
  - `<project-root>/.plan-executor/state/`
  - `<project-root>/.plan-executor/logs/`

### Team pipeline compatibility modes

Choose one compatibility mode when users request OMC-like orchestration depth.

- `teams-pipeline`:
  - stages: `plan -> design -> build -> verify -> release`
  - each stage must publish an artifact handoff
  - next stage cannot start without prior stage gate pass
- `swarm-style`:
  - many narrow micro-lanes (`3-6`) with small contracts
  - strict per-lane budget and quick merge cadence
  - use when work can be decomposed into independent shards
- `ultrapilot-style`:
  - one lead lane (`pilot`) plus specialist lanes (`2-3`)
  - pilot lane owns global decisions and reprioritization
  - use when requirements are volatile or ambiguous

### Worker runtime adapters

Select worker runtime by environment capability:

- `inline-worker` (default): run tasks in the current session.
- `worktree-worker`: assign one git worktree per lane for file isolation.
- `tmux-worker`: map each lane to a tmux pane/session when available.
- `process-worker`: execute lane commands through per-worker command templates (separate subprocess runtime).
- `ai-worker`: run lane prompts via `codex` CLI only.
- `delegate-worker`: enqueue lane commands to a file-backed delegate queue and wait for external worker responses.

Adapter rules:

- Fall back to `inline-worker` when requested runtime is unavailable.
- Require explicit file ownership when using shared workspace workers.
- Record chosen adapter in runbook and every checkpoint log.
- Use `scripts/bootstrap_team_manifest.py` to materialize mode + adapter + worker topology before execution.
- Default output path: `<project-root>/.plan-executor/team-manifests/`.
- `worktree-worker` executes lane commands in isolated git worktrees.
- `tmux-worker` executes lane commands in tmux sessions; fallback to inline when unavailable.
- `process-worker` executes lane commands with worker templates from team manifest (`command_template`).
- `ai-worker` executes lane prompts with worker `engine` (`codex`) and `command_template`.
- `ai-worker` supports per-worker `timeout_sec`, `max_retries`, `backoff_sec`.
- If selected AI CLI is not installed or not logged in (`codex login status` fail), lane is marked `ai-worker-unavailable-skip` and run continues.
- `ai-worker` writes standardized lane artifacts to `<project-root>/.plan-executor/artifacts/<run-id>/<lane-id>.json`.
- `delegate-worker` uses request/response files under `<project-root>/.plan-executor/delegates/`.
- `delegate-worker` supports per-worker `delegate_timeout_sec` (or `timeout_sec`) and `delegate_poll_sec` (or `poll_sec`).

### Message bus envelope

Use this message envelope for lane-to-integrator communication:

```json
{
  "from": "lane-2",
  "to": "integrator",
  "event": "checkpoint",
  "summary": "api validation patch complete",
  "artifacts": ["src/api/schema.ts", "tests/api/validation.test.ts"],
  "evidence": ["targeted-tests-pass", "lint-clean"],
  "status": "pass"
}
```

Runtime wiring:

- `scripts/runtime/orchestrator.py` emits message envelopes on:
  - `lane_assignment`
  - `lane_start`
  - `lane_done`
  - `checkpoint`
  - `finalize`/`failure`/`abort`
- Messages are stored at `<project-root>/.plan-executor/messages/<run-id>.jsonl`.
- `runtime_dashboard.py` and `runtime_report.py` surface `messages_total` and per-run `message_count`.

### Specialist registry and consensus runtime

- Specialist registry is persisted at `<project-root>/.plan-executor/agents/registry.json`.
- Runtime helper files:
  - `scripts/runtime/specialist_registry.py`
  - `scripts/runtime/consensus_engine.py`
  - `scripts/runtime/consensus_templates.py`
  - `scripts/agent_runtime_cli.py`
- Synthetic vote templates are stored at `<project-root>/.plan-executor/consensus/templates/*.json`.
- Orchestrator preflight validates lane `owner_role` against specialist ids/aliases and records unknown roles.

Use specialist/message/consensus CLI:

```bash
python C:/Users/JSC/.codex/skills/plan-executor/scripts/agent_runtime_cli.py --project-root <project-root> agents list
python C:/Users/JSC/.codex/skills/plan-executor/scripts/agent_runtime_cli.py --project-root <project-root> agents get --id planner
python C:/Users/JSC/.codex/skills/plan-executor/scripts/agent_runtime_cli.py --project-root <project-root> message send --run-id <run-id> --from-agent planner --to-agent integrator --kind note --content "lane ready"
python C:/Users/JSC/.codex/skills/plan-executor/scripts/agent_runtime_cli.py --project-root <project-root> message list --run-id <run-id> --limit 20
python C:/Users/JSC/.codex/skills/plan-executor/scripts/agent_runtime_cli.py --project-root <project-root> consensus create --run-id <run-id> --topic "merge policy" --participants planner,architect,qa --threshold 0.67 --quorum-ratio 0.66 --required-roles architect,qa --veto-roles qa --role-weight qa=1.3 --single-winner
python C:/Users/JSC/.codex/skills/plan-executor/scripts/agent_runtime_cli.py --project-root <project-root> consensus propose --run-id <run-id> --round-id <round-id> --author planner --content "option-a"
python C:/Users/JSC/.codex/skills/plan-executor/scripts/agent_runtime_cli.py --project-root <project-root> consensus critique --run-id <run-id> --round-id <round-id> --proposal-id <proposal-id> --author qa --content "risk note"
python C:/Users/JSC/.codex/skills/plan-executor/scripts/agent_runtime_cli.py --project-root <project-root> consensus vote --run-id <run-id> --round-id <round-id> --proposal-id <proposal-id> --author architect --role architect --decision approve --confidence 0.9
python C:/Users/JSC/.codex/skills/plan-executor/scripts/agent_runtime_cli.py --project-root <project-root> consensus finalize --run-id <run-id> --round-id <round-id> --threshold 0.67 --reject-threshold 0.67 --quorum-ratio 0.66 --required-roles architect,qa --allow-abstain
python C:/Users/JSC/.codex/skills/plan-executor/scripts/agent_runtime_cli.py --project-root <project-root> consensus evaluate --run-id <run-id> --round-id <round-id> --proposal-id <proposal-id> --required-decision accepted
python C:/Users/JSC/.codex/skills/plan-executor/scripts/bootstrap_consensus_template.py --project-root <project-root> --preset product-web-app-default --name web-default --force
```

Checkpoint consensus gate example:

```json
{
  "id": "checkpoint-1",
  "after_lanes": ["lane-1", "lane-2"],
  "gate_criteria": ["consensus-required"],
  "gate_commands": [],
  "consensus_gate": {
    "round_id": "",
    "proposal_id": "",
    "required_decision": "accepted",
    "finalize": true,
    "auto_create_round": true,
    "participants": ["planner", "architect", "qa"],
    "synthetic_votes_template": "web-default",
    "strict_template": false,
    "proposal_author": "planner",
    "proposal_content": "auto-generated-proposal",
    "auto_vote_mode": "approve-all",
    "auto_vote_confidence": 1.0
  }
}
```

`process-worker` manifest example:

```bash
python C:/Users/JSC/.codex/skills/plan-executor/scripts/bootstrap_team_manifest.py \
  --project-root <project-root> \
  --mode ultrapilot-style \
  --adapter process-worker \
  --workers 3 \
  --worker-cmd-template "cmd /c {cmd}"
```

`ai-worker` manifest example (codex only):

```bash
python C:/Users/JSC/.codex/skills/plan-executor/scripts/bootstrap_team_manifest.py \
  --project-root <project-root> \
  --mode swarm-style \
  --adapter ai-worker \
  --workers 2 \
  --ai-timeout-sec 180 \
  --ai-max-retries 1 \
  --ai-backoff-sec 1.5
```

AI worker E2E regression scenarios:

```bash
python C:/Users/JSC/.codex/skills/plan-executor/scripts/ai_worker_regression_test.py --project-root <project-root>
```

`delegate-worker` manifest + worker loop example:

```bash
python C:/Users/JSC/.codex/skills/plan-executor/scripts/bootstrap_team_manifest.py \
  --project-root <project-root> \
  --mode teams-pipeline \
  --adapter delegate-worker \
  --workers 2 \
  --delegate-timeout-sec 180 \
  --delegate-poll-sec 0.3
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_delegate_cli.py --project-root <project-root> --worker-id delegate-1 --role-filter planner serve --max-jobs 10 --idle-exit-sec 30
```

Delegate + consensus regression scenarios:

```bash
python C:/Users/JSC/.codex/skills/plan-executor/scripts/bootstrap_consensus_template.py --project-root <project-root> --preset product-web-app-default --name web-default --force
python C:/Users/JSC/.codex/skills/plan-executor/scripts/delegate_worker_regression_test.py --project-root <project-root>
python C:/Users/JSC/.codex/skills/plan-executor/scripts/consensus_regression_test.py --project-root <project-root>
python C:/Users/JSC/.codex/skills/plan-executor/scripts/consensus_checkpoint_regression_test.py --project-root <project-root>
python C:/Users/JSC/.codex/skills/plan-executor/scripts/consensus_autocreate_regression_test.py --project-root <project-root>
python C:/Users/JSC/.codex/skills/plan-executor/scripts/consensus_template_regression_test.py --project-root <project-root>
python C:/Users/JSC/.codex/skills/plan-executor/scripts/consensus_reconfigure_regression_test.py --project-root <project-root>
python C:/Users/JSC/.codex/skills/plan-executor/scripts/plan_search_regression_test.py --project-root <project-root>
python C:/Users/JSC/.codex/skills/plan-executor/scripts/frontstage_codex_teams_regression_test.py --project-root <project-root>
python C:/Users/JSC/.codex/skills/plan-executor/scripts/guardrails_regression_test.py --project-root <project-root>
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runbook_lint_regression_test.py --project-root <project-root>
python C:/Users/JSC/.codex/skills/plan-executor/scripts/entrypoint_lint_regression_test.py --project-root <project-root>
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_benchmark.py --project-root <project-root>
```

### Worker watchdog

- Emit heartbeat per active lane every major action.
- If a lane has no heartbeat for one cycle, mark `suspect`.
- If no heartbeat for two cycles, recycle lane:
  - reassign to backup role
  - replay latest runbook state
  - resume from last accepted checkpoint

### Independent runtime CLI

Use runtime commands for long-running or resumable executions:

```bash
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_cli.py \
  --project-root <project-root> start \
  --runbook <project-root>/.plan-executor/runbooks/<runbook>.json \
  --manifest <project-root>/.plan-executor/team-manifests/<manifest>.json \
  --adapter auto
```

`runtime_cli.py start` runs strict runbook lint by default and fails if `limits.command_guardrails` is missing or invalid.
Use `--skip-runbook-lint` only for local debugging.

```bash
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_cli.py --project-root <project-root> status --run-id <run-id>
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_cli.py --project-root <project-root> resume --run-id <run-id>
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_cli.py --project-root <project-root> abort --run-id <run-id> --reason "manual-stop"
```

```bash
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_runbook_lint.py --runbook <project-root>/.plan-executor/runbooks/<runbook>.json
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_runbook_lint.py --runbook <project-root>/.plan-executor/runbooks/<runbook>.json --json
```

Runtime state model:

- States: `running`, `failed`, `completed`, `aborted`.
- Events are appended to `<project-root>/.plan-executor/events/<run-id>.jsonl`.
- Run state snapshots are stored at `<project-root>/.plan-executor/state/<run-id>.json`.
- Scheduler executes ready DAG nodes concurrently up to `max_parallel_workers`.
- Checkpoint gates evaluate `gate_commands` and optional `consensus_gate`; any gate mismatch fails the run.

### Command guardrails (allow/deny)

Use this to enforce command safety/policy for lane and gate shell execution.
Built-in guardrail profiles: `dev`, `ci`, `prod`.

- `dev`: human-approval, deny risky commands (show command and ask operator approval).
- `ci`: enforce deny risky commands.
- `prod`: enforce + allowlist + deny risky commands.

Runbook `limits` example:

```json
{
  "limits": {
    "command_guardrails": {
      "enabled": true,
      "profile": "ci",
      "os_template": "auto",
      "include_os_risky_denylist": true,
      "mode": "enforce",
      "allowlist_patterns": ["^python\\s+-c\\b"],
      "denylist_patterns": ["\\bgit\\s+reset\\s+--hard\\b"],
      "phases": ["lane", "gate"]
    }
  }
}
```

- `mode=enforce`: blocked command fails run.
- `mode=audit`: command is allowed but guardrail audit evidence is recorded.
- `mode=human-approval`: flagged command is shown to operator for approval. In non-interactive runtime, default decision follows `approval_non_interactive_decision` (`deny` by default).
- `approval_safe_paths` / `approval_safe_path_prefixes` / `approval_safe_path_globs`: auto-approve destructive delete commands only when target path is in safe scope.
- `role_policies` / `environment_policies`: apply guardrail overrides by lane owner role and runtime environment (`role` override wins over `environment` override).
- `code_intelligence`: post-lane touched-file analysis (Python AST + TypeScript symbol scan) with `audit|enforce` mode.
- `os_template=auto`: host OS deny template is selected automatically.
- `bootstrap_runbook.py` default mapping: `speed -> dev`, `balanced -> ci`, `hardening -> prod`.

Human-approval policy example (safe cache cleanup with operator approval):

```json
{
  "limits": {
    "command_guardrails": {
      "enabled": true,
      "mode": "human-approval",
      "denylist_patterns": ["\\brm\\s+-rf\\b"],
      "approval_safe_path_prefixes": ["./temp", "./.cache"],
      "approval_non_interactive_decision": "deny",
      "phases": ["lane", "gate"]
    }
  }
}
```

Role/environment override example:

```json
{
  "limits": {
    "command_guardrails": {
      "enabled": true,
      "environment": "dev",
      "mode": "enforce",
      "denylist_patterns": ["^echo\\b"],
      "environment_policies": {
        "dev": {"mode": "human-approval", "approval_non_interactive_decision": "deny"},
        "ci": {"mode": "enforce"}
      },
      "role_policies": {
        "planner": {"mode": "human-approval", "approval_non_interactive_decision": "allow"}
      },
      "phases": ["lane", "gate"]
    }
  }
}
```

Code-intelligence example (enforce on high-risk file touches):

```json
{
  "limits": {
    "command_guardrails": {
      "enabled": true,
      "mode": "enforce",
      "allowlist_patterns": ["^python(\\.exe)?\\s+-c\\b"],
      "phases": ["lane", "gate"],
      "code_intelligence": {
        "enabled": true,
        "mode": "enforce",
        "max_total_code_files": 25,
        "max_high_risk_files": 2,
        "high_risk_symbol_threshold": 25,
        "high_risk_import_threshold": 20
      }
    }
  }
}
```

### Hybrid bridge (frontstage planner -> PE runtime)

Use this when you want external planning/consensus outputs but PE runtime execution/operations.

Frontstage multi-agent codex planner (parallel role discussion + consensus):

```bash
python C:/Users/JSC/.codex/skills/plan-executor/scripts/frontstage_codex_teams.py \
  --project-root <project-root> \
  --objective "Build feature X end-to-end" \
  --roles planner,architect,frontend,backend,qa \
  --rounds 2 \
  --debate-mode critique-revise \
  --critique-top-k 8 \
  --agent-runtime persistent \
  --worker-memory-lines 30 \
  --output <project-root>/.plan-executor/frontstage/plan.json
```

If codex CLI is unavailable or not logged in, this command fails fast (`codex-cli-not-found` or `codex-not-logged-in`).
`--agent-cmd-template` placeholders: `{prompt}`, `{role}`, `{phase}`, `{objective}`, `{round_index}`.

Persistent role workers:

- `--agent-runtime persistent` starts one long-lived worker process per role.
- Workers keep bounded role memory (`--worker-memory-lines`) and append it to prompts for consistency across rounds.
- Worker runtime implementation: `scripts/frontstage_role_worker.py`.

- Bridge command:
  - `scripts/hybrid_pipeline.py`
- Inputs:
  - `--planner-cmd`: external planner command outputting JSON to stdout
  - `--frontstage-plan`: frontstage JSON artifact file (`--omc-plan` is backward-compatible alias)
- Outputs:
  - normalized PE runbook + manifest
  - bridge log at `<project-root>/.plan-executor/logs/hybrid-bridge-<run-id>.json`
- `hybrid_pipeline.py` runs strict runbook lint after normalization and fails before artifact write/execute on lint errors.
- Use `--skip-runbook-lint` only for local debugging.

Prepare only:

```bash
python C:/Users/JSC/.codex/skills/plan-executor/scripts/hybrid_pipeline.py \
  --project-root <project-root> \
  --frontstage-plan <project-root>/.plan-executor/frontstage/plan.json \
  --adapter process-worker \
  --prepare-only
```

Prepare + execute:

```bash
python C:/Users/JSC/.codex/skills/plan-executor/scripts/hybrid_pipeline.py \
  --project-root <project-root> \
  --planner-cmd "python planner.py --format json" \
  --adapter process-worker
```

Frontstage codex teams -> hybrid execute:

```bash
python C:/Users/JSC/.codex/skills/plan-executor/scripts/hybrid_pipeline.py \
  --project-root <project-root> \
  --frontstage-plan <project-root>/.plan-executor/frontstage/plan.json \
  --adapter process-worker
```

### IPC control plane (double logging)

Use this when you need real-time mid-run replan/abort/consensus-policy-adjust while preserving replayability.

- Control server records every IPC message into JSONL control logs:
  - `<project-root>/.plan-executor/control/messages/<run-id>.jsonl`
  - `<project-root>/.plan-executor/control/ipc.log`
- Orchestrator consumes control messages and can apply `replan` / `consensus_reconfigure` during execution cycles.

```bash
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_control_cli.py --project-root <project-root> serve --host 127.0.0.1 --port 8765
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_control_cli.py --project-root <project-root> send --host 127.0.0.1 --port 8765 --run-id <run-id> --kind replan --payload-json "{\"reason\":\"mid-run\",\"update_lanes\":[{\"id\":\"lane-2\",\"commands\":[\"echo replanned\"]}]}"
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_control_cli.py --project-root <project-root> consensus-patch --transport ipc --host 127.0.0.1 --port 8765 --run-id <run-id> --checkpoint-id checkpoint-1 --patch-json "{\"threshold\":0.5,\"quorum_ratio\":0.5,\"required_roles\":[]}" --reason "mid-run-consensus-reconfigure"
```

Candidate-plan replan payload example:

```json
{
  "run_id": "<run-id>",
  "kind": "replan",
  "payload": {
    "reason": "candidate-search",
    "candidate_plans": [
      {
        "id": "plan-a",
        "confidence": 0.6,
        "risk_penalty": 5.0,
        "plan_patch": {
          "update_lanes": [{"id": "lane-2", "commands": ["echo option-a"]}],
          "dag": {"nodes": [{"id": "lane-1", "depends_on": []}, {"id": "lane-2", "depends_on": ["lane-1"]}]}
        }
      },
      {
        "id": "plan-b",
        "confidence": 0.8,
        "plan_patch": {
          "replace_pending_lanes": [{"id": "lane-2", "owner_role": "frontend", "commands": ["echo option-b"]}]
        }
      }
    ]
  }
}
```

Candidate scoring dry-run:

```bash
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_plan_cli.py score-candidates --input-json <candidate-json> --baseline-lanes lane-1,lane-2
```

### Queue daemon and operations dashboard

Use daemon queue mode for background scheduling:

```bash
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_daemon_cli.py --project-root <project-root> enqueue \
  --runbook <project-root>/.plan-executor/runbooks/<runbook>.json \
  --manifest <project-root>/.plan-executor/team-manifests/<manifest>.json \
  --adapter auto
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_daemon_cli.py --project-root <project-root> run-once --max-jobs 3
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_daemon_cli.py --project-root <project-root> recover --stale-sec 120 --max-retries 2
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_daemon_cli.py --project-root <project-root> stats
```

`runtime_daemon_cli.py enqueue` runs strict runbook lint by default and rejects unsafe/malformed runbooks before queueing.
`run-once` also re-validates runbook lint to block manual queue-file bypass.
Use `--skip-runbook-lint` only for local debugging.

Use dashboard view for operations visibility:

```bash
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_dashboard.py --project-root <project-root>
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_dashboard.py --project-root <project-root> --run-id <run-id> --events 20
```

### Non-core utilities

Use these utilities for maintenance and reporting (optional, not required for core execution):

```bash
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_maintenance.py --project-root <project-root> prune-runs --keep 30
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_maintenance.py --project-root <project-root> compact-events --max-events 200
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_maintenance.py --project-root <project-root> cleanup-worktrees
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_maintenance.py --project-root <project-root> clear-queue --bucket pending
```

```bash
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_report.py --project-root <project-root> --format json
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_report.py --project-root <project-root> --format md --output <project-root>/.plan-executor/logs/runtime-report.md
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_benchmark.py --project-root <project-root>
python C:/Users/JSC/.codex/skills/plan-executor/scripts/runtime_benchmark.py --project-root <project-root> --include-ai-worker --output-md <project-root>/.plan-executor/logs/runtime-benchmark.md
```

Report includes AI reliability fields:

- `ai_skip_total`, `ai_skip_rate`
- `ai_engine_success_rates` (codex/unknown)

Benchmark output:

- Fixed regression suite scorecard with weighted capabilities and runtime speed.
- JSON: `<project-root>/.plan-executor/logs/runtime-benchmark-<ts>.json`
- Markdown: `<project-root>/.plan-executor/logs/runtime-benchmark-<ts>.md`
- Baseline compare: writes `<project-root>/.plan-executor/logs/benchmark-latest.json` and emits delta fields on next run.

### Capability Matrix (PE v2 vs OMC)

Use this matrix to communicate boundaries clearly when users compare depth/features.

| Dimension | plan-executor (PE v2) | OMC |
| --- | --- | --- |
| Independent runtime | Python CLI without Claude/Codex session | Depends on Claude Code session |
| Queue | daemon `enqueue/run-once/serve/recover` | Not a native queue daemon |
| Dashboard | Terminal dashboard with runs/events/queue/messages | Event trace centric |
| Gate engine | Executes shell `gate_commands` with pass/fail | Mostly agent-driven manual validation |
| Worker adapters | `inline/worktree/tmux/process/ai(codex)` with fallback | Multi-agent provider/process orchestration |
| Resume/state | JSON state + JSONL events full replay | Session/tooling state persistence |
| Parallelism | `ThreadPoolExecutor` + DAG scheduler | Tool/agent parallel calls |
| Multi-agent model | Role emulation in one runtime | Real N-agent runtime/process model |
| Agent diversity | Specialist registry + aliases (single runtime type) | Many specialist agent implementations |
| Team communication | File-backed message bus + consensus artifacts | Native SendMessage/broadcast IPC |
Upgrade focus from this matrix:

- Keep PE strong in infra/ops (`queue`, `gate`, `resume`, `dashboard`).
- Narrow OMC gaps by improving:
  - specialist depth (more role SOP and checks)
  - message bus semantics (routing policy, SLA, retry)
  - consensus policy (multi-round, veto/quorum strategies)

## Response Shape

For non-trivial tasks, use this sequence:

1. `Plan`
2. `Execution Updates`
3. `Verification`
4. `Final Result`

