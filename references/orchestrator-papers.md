# Orchestrator Papers Reference

Use this file when the user asks for paper-backed rationale behind orchestration rules.

## Plan and Reasoning Strategy

- ReAct (Yao et al., 2022): https://arxiv.org/abs/2210.03629
  - Implication: Interleave reasoning and acting, then update plan with tool feedback instead of pure offline planning.
- Plan-and-Solve Prompting (Wang et al., 2023): https://arxiv.org/abs/2305.04091
  - Implication: Add explicit planning stage before execution to reduce missing-step failures.
- Tree of Thoughts (Yao et al., 2023): https://arxiv.org/abs/2305.10601
  - Implication: Generate and evaluate multiple candidate plans for hard tasks.
- Language Agent Tree Search (Zhou et al., 2023): https://arxiv.org/abs/2310.04406
  - Implication: Use search + value signals to backtrack and recover from weak branches.

## Feedback and Self-Correction

- CRITIC (Gou et al., 2023): https://arxiv.org/abs/2305.11738
  - Implication: Improve outputs with tool-interactive critique rather than self-judgment only.
- Large Language Models Cannot Self-Correct Reasoning Yet? (Huang et al., 2023): https://arxiv.org/abs/2310.01798
  - Implication: Require external verification before accepting self-corrections.
- Self-Refine (Madaan et al., 2023): https://arxiv.org/abs/2303.17651
  - Implication: Use iterative refine loops, but keep quality gates grounded in external checks.
- Reflexion (Shinn et al., 2023): https://arxiv.org/abs/2303.11366
  - Implication: Store verbal feedback/memory and reuse it in future trials.

## Multi-Agent Orchestration

- CAMEL (Li et al., 2023): https://arxiv.org/abs/2303.17760
  - Implication: Role specialization improves coordination when roles and constraints are explicit.
- ChatDev (Qian et al., 2023): https://arxiv.org/abs/2307.07924
  - Implication: SOP-style phase control reduces chaotic multi-agent interactions.
- MetaGPT (Hong et al., 2023): https://arxiv.org/abs/2308.00352
  - Implication: Standardized interfaces/contracts are key for agent handoffs.
- AutoGen (Wu et al., 2023): https://arxiv.org/abs/2308.08155
  - Implication: Conversation programming enables orchestrator-driven delegation and validation loops.

## Parallelism and Execution Interfaces

- LLMCompiler (Kim et al., 2024): https://arxiv.org/abs/2312.04511
  - Implication: Compile tasks into dependency-aware execution graphs for parallel speedups.
- Graph of Thoughts (Besta et al., 2023): https://arxiv.org/abs/2308.09687
  - Implication: Represent thought dependencies as a graph and support recombination/backtracking.
- Voyager (Wang et al., 2023): https://arxiv.org/abs/2305.16291
  - Implication: Keep an explicit skill library and episodic memory for continual improvement.
- SWE-agent (Yang et al., 2024): https://arxiv.org/abs/2405.15793
  - Implication: Agent-computer interface design materially affects autonomous task success.

## Practical Translation for This Skill

- Prefer DAG-based lane scheduling over ad-hoc parallelism.
- Require tool/test evidence at checkpoints.
- Keep structured lane contracts and explicit merge gates.
- Track orchestration metrics and trigger replanning on repeated stalls.

## Rule-to-Paper Mapping (for SKILL.md controls)

- `DAG format example` -> LLMCompiler, Graph of Thoughts
  - Why: dependency-aware task graphs improve parallel planning and recomposition.
- `Checkpoint acceptance criteria` -> CRITIC, SWE-agent, LLMs Cannot Self-Correct
  - Why: external tool feedback and explicit interfaces outperform self-judgment-only loops.
- `Agent card schema template` -> MetaGPT, AutoGen, ChatDev
  - Why: role/interface standardization reduces coordination entropy in multi-agent workflows.
- `Conflict decision matrix` -> MetaGPT, AutoGen
  - Why: deterministic precedence rules stabilize handoffs under shared-target contention.
- `max_replan=3` -> Language Agent Tree Search, Reflexion
  - Why: bounded search/reflection avoids infinite loops while preserving recovery behavior.

## Role Preset Evidence

- `Preset A: metagpt-swe-line` -> MetaGPT
  - Evidence: paper defines role specialization around Product Manager, Architect, Project Manager, Engineer, QA Engineer.
  - Sources:
    - https://arxiv.org/abs/2308.00352
    - https://ar5iv.labs.arxiv.org/html/2308.00352

- `Preset B: chatdev-waterfall` -> ChatDev
  - Evidence: paper describes phase-driven role collaboration such as CEO/CPO/CTO, Programmer, Art Designer, Reviewer, Tester.
  - Sources:
    - https://arxiv.org/abs/2307.07924
    - https://ar5iv.labs.arxiv.org/html/2307.07924

- `Preset C: autogen-duo-plus` -> AutoGen
  - Evidence: paper introduces AssistantAgent, UserProxyAgent, and GroupChatManager patterns.
  - Sources:
    - https://arxiv.org/abs/2308.08155
    - https://ar5iv.labs.arxiv.org/html/2308.08155

- `Preset D: product-web-app` -> Derived composition
  - Evidence: combines MetaGPT role specialization and ChatDev design/coding/testing separation into PM/Designer/FE/BE/QA mapping.
  - Sources:
    - https://arxiv.org/abs/2308.00352
    - https://arxiv.org/abs/2307.07924

## Orchestration Depth Mapping

- `teams-pipeline` mode -> ChatDev, MetaGPT
  - Why: phase-driven software pipeline with explicit role-stage artifacts.
  - Sources:
    - https://arxiv.org/abs/2307.07924
    - https://arxiv.org/abs/2308.00352

- `swarm-style` mode -> CAMEL, AutoGen
  - Why: many specialized conversational agents coordinated by protocol and tool messages.
  - Sources:
    - https://arxiv.org/abs/2303.17760
    - https://arxiv.org/abs/2308.08155

- `ultrapilot-style` mode -> AutoGen (group manager + agents), MetaGPT (central planning)
  - Why: lead agent routes decisions while specialists execute scoped contracts.
  - Sources:
    - https://arxiv.org/abs/2308.08155
    - https://arxiv.org/abs/2308.00352

- `worker watchdog + message envelope` -> SWE-agent, CRITIC
  - Why: explicit interface and external feedback loops improve recovery and reliability.
  - Sources:
    - https://arxiv.org/abs/2405.15793
    - https://arxiv.org/abs/2305.11738

## Communication and Consensus Guidance

- `specialist registry` -> MetaGPT, CAMEL
  - Why: explicit role identity and contracts improve coordination reliability.
  - Sources:
    - https://arxiv.org/abs/2308.00352
    - https://arxiv.org/abs/2303.17760

- `message bus envelope` -> AutoGen, SWE-agent
  - Why: explicit message interfaces and tool-facing interaction traces improve operational observability.
  - Sources:
    - https://arxiv.org/abs/2308.08155
    - https://arxiv.org/abs/2405.15793

- `consensus round (propose/critique/vote/finalize)` -> CRITIC, ChatDev, AutoGen
  - Why: critique loops + structured turn-taking reduce ad-hoc merge decisions.
  - Sources:
    - https://arxiv.org/abs/2305.11738
    - https://arxiv.org/abs/2307.07924
    - https://arxiv.org/abs/2308.08155
