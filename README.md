# plan-executor

Multi-agent orchestration runtime for converting objectives into executable plans and driving them to completion.

## What it does

plan-executor converts broad objectives into finite execution plans using parallel AI agents (Codex, Gemini) with consensus, then executes those plans via DAG-based lane orchestration with guardrails, checkpoints, and recovery.

**Frontstage** (planning): Multiple agents run in parallel through propose, critique, revise, and consensus phases to produce a validated runbook.

**Runtime** (execution): The orchestrator drives execution lane by lane, routing each step to the appropriate engine (Codex, Gemini, or shell), enforcing command guardrails, verifying gates, and persisting state so interrupted runs can be resumed.

## Requirements

- Python 3.10+
- No pip dependencies (stdlib only)
- Optional: Codex CLI (primary AI engine), Gemini CLI (secondary/fallback)

## Quick Start

```bash
git clone https://github.com/jsc7727/plan-executor.git
cd plan-executor

# Run a sample runbook with shell engine (no AI CLI required)
python scripts/runtime_cli.py --project-root . start --runbook runbooks/sample-speed-runbook.json

# Run with Codex engine (requires Codex CLI)
python scripts/runtime_cli.py --project-root . start --runbook runbooks/sample-speed-runbook.json --engine codex

# View dashboard
python scripts/runtime_dashboard.py --project-root .
```

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     FRONTSTAGE (Planning)                    │
│                                                             │
│  frontstage_codex_teams.py                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│  │ Propose  │→ │ Critique │→ │  Revise  │→ │ Consensus │  │
│  │ (N roles)│  │ (top-K)  │  │ (owners) │  │ (scoring) │  │
│  └──────────┘  └──────────┘  └──────────┘  └───────────┘  │
│        ↓ frontstage plan JSON                               │
│  hybrid_pipeline.py  (plan → runbook + manifest conversion) │
└──────────────────────┬──────────────────────────────────────┘
                       ↓
┌──────────────────────┴──────────────────────────────────────┐
│                     RUNTIME (Execution)                      │
│                                                             │
│  orchestrator.py  (DAG scheduler, ThreadPoolExecutor)       │
│        │                                                    │
│        ├── worker_adapters.py                               │
│        │   ├── InlineWorkerAdapter    (shell subprocess)    │
│        │   ├── ProcessWorkerAdapter   (isolated process)    │
│        │   ├── WorktreeWorkerAdapter  (git worktree)        │
│        │   ├── TmuxWorkerAdapter      (tmux pane)           │
│        │   ├── AiCliWorkerAdapter     (Codex/Gemini CLI)    │
│        │   └── DelegateWorkerAdapter  (async queue)         │
│        │                                                    │
│        ├── gate_engine.py        (checkpoint verification)  │
│        ├── command_guardrails.py (dangerous cmd blocking)   │
│        ├── consensus_engine.py   (weighted voting protocol) │
│        ├── event_store.py        (state / events / logs)    │
│        ├── message_bus.py        (inter-agent messaging)    │
│        ├── control_plane.py      (IPC / live reconfiguration)│
│        └── plan_search.py        (replan candidate scoring) │
└─────────────────────────────────────────────────────────────┘
```

## Core Concepts

### Runbook

A runbook is the execution plan. It defines **what** to run, in **what order**, with **what constraints**.

```json
{
  "meta": {
    "preset": "product-web-app",
    "profile": "balanced",
    "mode": "parallel",
    "max_parallel_workers": 4
  },
  "dag": {
    "nodes": [
      { "id": "lane-1", "depends_on": [] },
      { "id": "lane-2", "depends_on": ["lane-1"] }
    ]
  },
  "lanes": [
    {
      "id": "lane-1",
      "owner_role": "frontend",
      "scope": "Build React components",
      "commands": ["npm run build", "npm test"],
      "done_criteria": ["checkpoint accepted by integrator"]
    }
  ],
  "checkpoints": [
    {
      "id": "checkpoint-1",
      "after_lanes": ["lane-1", "lane-2"],
      "gate_criteria": ["targeted-tests-pass"],
      "gate_commands": ["npm run test"]
    }
  ],
  "limits": {
    "max_replan": 3,
    "stall_rounds_threshold": 2,
    "verification_pass_rate_min": 0.7,
    "fallback_chain": "codex,gemini,shell",
    "command_guardrails": {
      "enabled": true,
      "profile": "ci",
      "mode": "enforce"
    }
  }
}
```

**Key fields:**
- `dag.nodes`: Dependency graph. Lanes with no dependencies run first; others wait.
- `lanes`: Each lane has an `owner_role`, shell `commands`, and `done_criteria`.
- `checkpoints`: Gates that evaluate after specified lanes complete. Can include consensus voting.
- `limits`: Global constraints — replan budget, stall detection, guardrail config, fallback chain.

### Manifest

A manifest defines **who** executes the lanes — which AI engine, command template, and timeout.

```json
{
  "meta": {
    "adapter": "ai-worker",
    "ai_engine": "codex"
  },
  "workers": [
    {
      "id": "worker-1",
      "role": "frontend",
      "engine": "codex",
      "command_template": "codex exec \"{cmd}\"",
      "timeout_sec": 180,
      "max_retries": 1,
      "backoff_sec": 1.5
    }
  ]
}
```

Workers are matched to lanes by `role`. The orchestrator injects worker config (engine, template, timeout) into lane runtime payloads.

### Lanes and DAG Execution

The orchestrator builds a dependency map from `dag.nodes` and executes lanes in topological order:

1. Find all lanes whose dependencies are satisfied ("ready" lanes)
2. Execute ready lanes in parallel via `ThreadPoolExecutor`
3. On completion, check for newly unblocked lanes
4. At checkpoints, run gate verification before proceeding
5. On failure, trigger replan candidate search (bounded by `max_replan`)

### Engine System

plan-executor supports three execution engines:

| Engine | CLI | Use case |
|--------|-----|----------|
| **shell** | `subprocess.run()` | Default. Runs commands directly. |
| **codex** | `codex exec "{cmd}"` | Primary AI engine. Codex interprets and executes. |
| **gemini** | `gemini -p "{cmd}" --yolo` | Secondary AI engine. Fallback when Codex unavailable. |

**Engine priority:** Manifest worker engine > CLI `--engine` flag > default (shell).

### Fallback Chain

When an AI engine is unavailable (binary missing, API key empty, health check fails), the fallback chain determines what to try next.

```
limits.fallback_chain: "codex,gemini,shell"
```

- Default (no config): primary engine only. If unavailable, lane is skipped.
- With fallback chain: tries engines in order. `shell` sentinel delegates to `InlineWorkerAdapter`.
- Opt-in per runbook via `limits.fallback_chain`.

### Hybrid Failure Handling

When a command fails, the failure is classified:

| Type | Trigger | Handling |
|------|---------|----------|
| **Infrastructure** | Timeout, SIGKILL, SIGSEGV, binary not found, empty stderr | Deterministic: PE retries with backoff, then tries remaining engines in `fallback_chain`. Template is synchronized on engine switch so subsequent commands use the correct wrapper. |
| **Logic** | Nonzero exit with meaningful stderr (test fail, build error) | AI-judged: Builds repair prompt from stderr/stdout, sends to AI engine for diagnosis and fix. Bounded by `max_replan`. Skipped if infra fallback already resolved the failure. |

Classification logic (`_classify_failure`):
- Timeout → infrastructure
- Return codes 124, 125, 126, 127, 137, 139 → infrastructure
- Empty stderr with nonzero exit → infrastructure
- Everything else → logic

**Infrastructure fallback during execution:** When an infrastructure failure occurs mid-lane, PE walks the remaining `fallback_chain` engines. On success, `effective_engine` and `template` are both updated so that all subsequent commands in the same lane run through the new engine. This prevents silent misrouting where a fallback succeeds for one command but later commands revert to the failed engine's template.

The repair prompt is sanitized (`_sanitize_for_prompt`) to prevent shell injection before being passed to the AI engine.

### Command Guardrails

Every command passes through guardrail evaluation before execution.

**Profiles:**
| Profile | Mode | Behavior |
|---------|------|----------|
| `dev` | human-approval | Prompts user for risky commands |
| `ci` | enforce | Blocks denylist, allows everything else |
| `prod` | enforce | Allowlist-only + denylist |

**Denylist (all profiles):** `git reset --hard`, `git clean -fdx`, `rm -rf /`, `format`, `shutdown`, `reboot`, `mkfs`, etc.

**Safe-path auto-allow:** Commands targeting files in `output_contract.files_changed` are auto-approved in enforce mode.

Guardrails apply to both lane commands and gate commands (`phases: ["lane", "gate"]`).

### Gate Engine (Checkpoints)

Checkpoints verify that a group of lanes produced correct results before the run proceeds.

```json
{
  "id": "checkpoint-1",
  "after_lanes": ["lane-1", "lane-2"],
  "gate_criteria": ["targeted-tests-pass", "lint-clean"],
  "gate_commands": ["npm run test"],
  "consensus_gate": {
    "topic": "Merge quality check",
    "participants": ["integrator", "qa"],
    "threshold": 0.67
  }
}
```

**Flow:**
1. Run `gate_commands` (with guardrail checks)
2. Match output against `gate_criteria` keywords
3. If `consensus_gate` present, create a consensus round and vote
4. Return pass/fail with evidence

### Consensus Engine

Weighted multi-agent voting protocol for decision-making.

**Scoring:**
```
approve_score = Σ(confidence × role_weight) for approve votes
reject_score  = Σ(confidence × role_weight) for reject votes
critique_penalty = Σ(severity_weight × role_weight) for critiques
final_score = approve_score - reject_score - critique_penalty
```

**Decision rules:**
- **Accepted:** `score ≥ threshold` AND `quorum ≥ quorum_ratio`
- **Veto:** Any `veto_role`'s reject blocks the proposal regardless of score
- **Required roles:** All `required_roles` must vote approve

**Template votes:** Synthetic vote templates can be loaded from `.plan-executor/consensus/templates/` for automated gate decisions.

### Frontstage Pipeline (Multi-Agent Planning)

`frontstage_codex_teams.py` orchestrates parallel AI agents to generate execution plans:

**Per round:**
1. **Propose:** All roles generate proposals in parallel (ThreadPoolExecutor)
2. **Critique:** All roles critique top-K proposals with severity ratings
3. **Revise:** Proposal owners revise based on critiques + self-vote (0.8 confidence)
4. **Score:** Votes aggregated → proposals accepted/rejected by threshold + quorum

**Agent runtime modes:**
- `persistent`: Long-lived subprocess workers (`frontstage_role_worker.py`) with stdin/stdout JSONL IPC and cross-phase memory
- `oneshot`: Fresh subprocess per call (fallback)

**Output:** Frontstage plan JSON with stages, consensus scores, and execution trace.

### Plan Search (Replan)

When lanes fail, `plan_search.py` scores replan candidates:

- **Commands coverage:** % of lanes with commands
- **Checkpoint coverage:** gates defined for lanes
- **DAG risk:** cycle detection, unreachable lanes
- **Total score:** weighted sum → best candidate selected

Bounded by `limits.max_replan` to prevent infinite loops.

### State and Events

All state is persisted under `.plan-executor/`:

```
.plan-executor/
├── events/{run_id}.jsonl    # Append-only event log
├── state/{run_id}.json      # Current run state snapshot
├── messages/{run_id}.jsonl  # Inter-agent messages
├── control/messages/        # Control plane IPC messages
├── artifacts/{run_id}/      # Lane output artifacts
├── consensus/{run_id}/      # Consensus round data
│   └── templates/           # Synthetic vote templates
├── delegates/               # Async work queue
│   ├── pending/
│   ├── claimed/
│   └── completed/
├── agents/registry.json     # Specialist registry
├── worktrees/{run_id}/      # Git worktree isolation
├── runbooks/                # Runbook files
├── team-manifests/          # Worker manifest files
└── logs/                    # Execution logs
```

**Run lifecycle:** `pending → running → completed | failed | aborted`

**Event types:** `preflight`, `lane_start`, `lane_done`, `checkpoint`, `replan_candidate_selected`, `consensus_reconfigured`, `message_error`

Interrupted runs can be resumed via `runtime_cli.py resume --run-id <id>`.

### Specialist Registry

14 built-in roles: orchestrator, integrator, planner, architect, security-reviewer, designer, frontend, backend, qa, devops-engineer, data-engineer, performance-engineer, reliability-engineer, documentation-writer.

Custom specialists can be added to `.plan-executor/agents/registry.json`.

## CLI Reference

### runtime_cli.py

| Command | Description |
|---------|-------------|
| start | Start a new run from a runbook |
| status | Show run status and recent events |
| resume | Resume a paused or interrupted run |
| abort | Abort a running or paused run |
| runs | List all runs |

```bash
python scripts/runtime_cli.py --project-root . start --runbook <path> [--manifest <path>] [--engine codex] [--adapter ai-worker]
python scripts/runtime_cli.py --project-root . status --run-id <id> [--events 20] [--json]
python scripts/runtime_cli.py --project-root . resume --run-id <id>
python scripts/runtime_cli.py --project-root . abort --run-id <id> [--reason "..."]
python scripts/runtime_cli.py --project-root . runs
```

### runtime_daemon_cli.py

| Command | Description |
|---------|-------------|
| enqueue | Add a runbook to the daemon queue |
| run-once | Process one queued item and exit |
| serve | Run the daemon loop continuously |
| recover | Recover interrupted runs from a previous session |
| stats | Show daemon queue and run statistics |

```bash
python scripts/runtime_daemon_cli.py --project-root . enqueue --runbook <path>
python scripts/runtime_daemon_cli.py --project-root . serve
python scripts/runtime_daemon_cli.py --project-root . recover
python scripts/runtime_daemon_cli.py --project-root . stats
```

### runtime_control_cli.py

| Command | Description |
|---------|-------------|
| serve | Start the control server |
| send | Send a control message to a running run |
| consensus-patch | Apply a consensus patch to a run |
| enqueue | Enqueue a runbook via the control server |
| stats | Show control server statistics |
| list | List control messages for a run |

```bash
python scripts/runtime_control_cli.py --project-root . serve
python scripts/runtime_control_cli.py --project-root . send --run-id <id> --kind replan --payload-json '{"reason":"..."}'
python scripts/runtime_control_cli.py --project-root . stats
```

### agent_runtime_cli.py

| Subcommand | Description |
|------------|-------------|
| agents list | List all registered specialists |
| agents get | Get a specific specialist by ID |
| message send | Send inter-agent message |
| message list | List messages for a run |
| consensus create | Create a consensus round |
| consensus vote | Cast a vote |
| consensus finalize | Finalize and score a round |

### frontstage_codex_teams.py

```bash
python scripts/frontstage_codex_teams.py \
  --project-root . \
  --objective "Build a REST API with auth" \
  --roles planner,architect,backend,qa \
  --rounds 2 \
  --debate-mode critique-revise \
  --agent-cmd-template 'codex exec "{prompt}"'
```

### hybrid_pipeline.py

```bash
python scripts/hybrid_pipeline.py \
  --project-root . \
  --frontstage-plan .plan-executor/frontstage/plan.json \
  --runbook-out .plan-executor/runbooks/generated.json \
  --manifest-out .plan-executor/team-manifests/generated.json \
  --ai-engine codex
```

### runtime_dashboard.py

| Flag | Description |
|------|-------------|
| --run-id | Show dashboard for a specific run |
| --events N | Show last N events (default 10) |
| --json | Output in JSON format |

```bash
python scripts/runtime_dashboard.py --project-root .
python scripts/runtime_dashboard.py --project-root . --run-id <id> --events 20
python scripts/runtime_dashboard.py --project-root . --json
```

## Regression Tests

7 test suites, 20 test cases:

```bash
python scripts/guardrails_regression_test.py --project-root .         # 7 cases
python scripts/runbook_lint_regression_test.py --project-root .       # 3 cases
python scripts/consensus_regression_test.py --project-root .          # 2 cases
python scripts/plan_search_regression_test.py --project-root .        # 2 cases
python scripts/frontstage_codex_teams_regression_test.py --project-root .  # 1 case (multi-phase)
python scripts/delegate_worker_regression_test.py --project-root .    # 1 case (E2E)
python scripts/ai_worker_regression_test.py --project-root .          # 3 cases (codex/gemini/fallback)
```

## Roadmap

See [ROADMAP.md](ROADMAP.md).

## License

MIT
