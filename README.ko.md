# plan-executor

목표를 실행 계획으로 변환하고 완료까지 구동하는 멀티 에이전트 오케스트레이션 런타임.

## 기능 요약

plan-executor는 병렬 AI 에이전트(Codex)와 합의 프로토콜을 사용하여 광범위한 목표를 유한한 실행 계획으로 변환한 뒤, DAG 기반 레인 오케스트레이션으로 가드레일, 체크포인트, 복구 기능과 함께 실행합니다.

**프론트스테이지** (계획 단계): 여러 에이전트가 제안 → 비평 → 수정 → 합의 단계를 병렬로 수행하여 검증된 런북을 생성합니다.

**런타임** (실행 단계): 오케스트레이터가 레인별로 실행을 구동하며, 적절한 엔진(Codex, shell)으로 라우팅하고, 커맨드 가드레일을 적용하고, 게이트를 검증하며, 상태를 영속화하여 중단된 실행을 재개할 수 있습니다.

## 요구사항

- Python 3.10+
- pip 의존성 없음 (표준 라이브러리만 사용)
- 선택: Codex CLI (주 AI 엔진)

## 빠른 시작

```bash
git clone https://github.com/jsc7727/plan-executor.git
cd plan-executor

# shell 엔진으로 샘플 런북 실행 (AI CLI 불필요)
python scripts/runtime_cli.py --project-root . start --runbook runbooks/sample-speed-runbook.json

# Codex 엔진으로 실행 (Codex CLI 필요)
python scripts/runtime_cli.py --project-root . start --runbook runbooks/sample-speed-runbook.json --engine codex

# 대시보드 확인
python scripts/runtime_dashboard.py --project-root .
```

## 아키텍처 개요

```
┌─────────────────────────────────────────────────────────────┐
│                   프론트스테이지 (계획)                        │
│                                                             │
│  frontstage_codex_teams.py                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│  │   제안   │→ │   비평   │→ │   수정   │→ │   합의    │  │
│  │ (N 역할) │  │ (top-K)  │  │ (작성자) │  │ (스코어링)│  │
│  └──────────┘  └──────────┘  └──────────┘  └───────────┘  │
│        ↓ 프론트스테이지 계획 JSON                             │
│  hybrid_pipeline.py  (계획 → 런북 + 매니페스트 변환)          │
└──────────────────────┬──────────────────────────────────────┘
                       ↓
┌──────────────────────┴──────────────────────────────────────┐
│                      런타임 (실행)                            │
│                                                             │
│  orchestrator.py  (DAG 스케줄러, ThreadPoolExecutor)         │
│        │                                                    │
│        ├── worker_adapters.py                               │
│        │   ├── InlineWorkerAdapter    (shell 서브프로세스)    │
│        │   ├── ProcessWorkerAdapter   (격리 프로세스)         │
│        │   ├── WorktreeWorkerAdapter  (git worktree)        │
│        │   ├── TmuxWorkerAdapter      (tmux 패인)           │
│        │   ├── AiCliWorkerAdapter     (Codex CLI)           │
│        │   └── DelegateWorkerAdapter  (비동기 큐)            │
│        │                                                    │
│        ├── gate_engine.py        (체크포인트 검증)            │
│        ├── command_guardrails.py (위험 명령어 차단)           │
│        ├── consensus_engine.py   (가중 투표 프로토콜)         │
│        ├── event_store.py        (상태 / 이벤트 / 로그)      │
│        ├── message_bus.py        (에이전트 간 메시징)         │
│        ├── control_plane.py      (IPC / 실시간 재설정)       │
│        └── plan_search.py        (재계획 후보 점수화)         │
└─────────────────────────────────────────────────────────────┘
```

## 핵심 개념

### 런북 (Runbook)

런북은 실행 계획입니다. **무엇을**, **어떤 순서로**, **어떤 제약 조건으로** 실행할지 정의합니다.

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
      "scope": "React 컴포넌트 빌드",
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
    "fallback_chain": "codex,shell",
    "command_guardrails": {
      "enabled": true,
      "profile": "ci",
      "mode": "enforce"
    }
  }
}
```

**주요 필드:**
- `dag.nodes`: 의존성 그래프. 의존성 없는 레인이 먼저 실행되고, 나머지는 대기.
- `lanes`: 각 레인은 `owner_role`, shell `commands`, `done_criteria`를 가짐.
- `checkpoints`: 지정된 레인 완료 후 평가하는 게이트. 합의 투표 포함 가능.
- `limits`: 전역 제약 — 재계획 예산, 정체 감지, 가드레일 설정, 폴백 체인.

### 매니페스트 (Manifest)

매니페스트는 레인을 **누가** 실행하는지 정의합니다 — AI 엔진, 커맨드 템플릿, 타임아웃.

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

워커는 `role`로 레인과 매칭됩니다. 오케스트레이터가 워커 설정(엔진, 템플릿, 타임아웃)을 레인 런타임 페이로드에 주입합니다.

### 레인과 DAG 실행

오케스트레이터는 `dag.nodes`에서 의존성 맵을 구축하고 위상 정렬 순서로 레인을 실행합니다:

1. 의존성이 충족된 모든 레인 찾기 ("준비" 상태)
2. `ThreadPoolExecutor`로 준비된 레인들을 병렬 실행
3. 완료 시 새로 해제된 레인 확인
4. 체크포인트에서 게이트 검증 후 진행
5. 실패 시 재계획 후보 탐색 (`max_replan`으로 제한)

### 엔진 시스템

plan-executor는 두 가지 실행 엔진을 지원합니다:

| 엔진 | CLI | 용도 |
|------|-----|------|
| **shell** | `subprocess.run()` | 기본값. 명령어 직접 실행. |
| **codex** | `codex exec "{cmd}"` | 주 AI 엔진. Codex가 해석하고 실행. |

**엔진 우선순위:** 매니페스트 워커 엔진 > CLI `--engine` 플래그 > 기본값(shell).

### 폴백 체인 (Fallback Chain)

AI 엔진이 사용 불가능할 때 (바이너리 없음, 로그인 체크 실패), 폴백 체인이 다음에 시도할 엔진을 결정합니다.

```
limits.fallback_chain: "codex,shell"
```

- **기본값** (설정 없음): 주 엔진만 사용. 불가하면 레인 스킵.
- **폴백 체인 설정 시**: 순서대로 엔진 시도. `shell`은 `InlineWorkerAdapter`로 위임.
- 런북의 `limits.fallback_chain`으로 런북별 설정 가능.

### 하이브리드 실패 처리 (Hybrid Failure Handling)

명령어가 실패하면, 실패 유형이 분류됩니다:

| 유형 | 트리거 | 처리 방식 |
|------|--------|-----------|
| **인프라** | 타임아웃, SIGKILL, SIGSEGV, 바이너리 없음, stderr 비어있음 | 결정론적: PE가 백오프로 재시도 후 `fallback_chain`의 나머지 엔진을 순서대로 시도. 엔진 전환 시 템플릿도 동기화되어 후속 커맨드가 올바른 래퍼로 실행됨. |
| **로직** | 의미있는 stderr와 함께 비정상 종료 (테스트 실패, 빌드 에러) | AI 판단: stderr/stdout에서 수리 프롬프트를 생성한 뒤, 현재 실행 fallback 상태와 무관하게 전용 Codex 수리 엔진(`codex`)으로 수리를 시도. `max_replan`으로 제한되며 Codex 수리가 불가능하면 수리 루프를 건너뜀. |

분류 로직 (`_classify_failure`):
- 타임아웃 → 인프라
- 리턴코드 124, 125, 126, 127, 137, 139 → 인프라
- stderr 비어있고 비정상 종료 → 인프라
- 그 외 → 로직

**실행 중 인프라 fallback:** 레인 실행 도중 인프라 실패가 발생하면 PE가 `fallback_chain`의 나머지 엔진을 순서대로 시도합니다. 성공 시 `effective_engine`과 `template`이 모두 갱신되어, 같은 레인의 후속 커맨드가 새 엔진을 통해 실행됩니다. 이를 통해 fallback이 한 커맨드에서 성공했지만 이후 커맨드가 실패한 엔진의 템플릿으로 되돌아가는 조용한 오류 라우팅을 방지합니다.

수리 프롬프트는 셸 인젝션 방지를 위해 `_sanitize_for_prompt`로 정화되며, shell fallback 경로에서 자연어 프롬프트가 직접 실행되지 않도록 차단됩니다.

### 커맨드 가드레일 (Command Guardrails)

모든 명령어는 실행 전에 가드레일 평가를 통과합니다.

**프로필:**
| 프로필 | 모드 | 동작 |
|--------|------|------|
| `dev` | human-approval | 위험 명령어에 대해 사용자에게 확인 요청 |
| `ci` | enforce | 거부 목록 차단, 나머지 허용 |
| `prod` | enforce | 허용 목록만 + 거부 목록 |

**거부 목록 (전 프로필 공통):** `git reset --hard`, `git clean -fdx`, `rm -rf /`, `format`, `shutdown`, `reboot`, `mkfs` 등.

**안전 경로 자동 허용:** `output_contract.files_changed`에 포함된 파일 대상 명령어는 enforce 모드에서 자동 승인.

가드레일은 레인 명령어와 게이트 명령어 모두에 적용됩니다 (`phases: ["lane", "gate"]`).

### 게이트 엔진 (체크포인트)

체크포인트는 레인 그룹이 올바른 결과를 생산했는지 실행 진행 전에 검증합니다.

```json
{
  "id": "checkpoint-1",
  "after_lanes": ["lane-1", "lane-2"],
  "gate_criteria": ["targeted-tests-pass", "lint-clean"],
  "gate_commands": ["npm run test"],
  "consensus_gate": {
    "topic": "병합 품질 확인",
    "participants": ["integrator", "qa"],
    "threshold": 0.67
  }
}
```

**흐름:**
1. `gate_commands` 실행 (가드레일 적용)
2. 출력에서 `gate_criteria` 키워드 매칭
3. `consensus_gate` 있으면 합의 라운드 생성 후 투표
4. 증거와 함께 통과/실패 반환

### 합의 엔진 (Consensus Engine)

의사결정을 위한 가중 멀티 에이전트 투표 프로토콜.

**점수 계산:**
```
승인 점수 = Σ(신뢰도 × 역할 가중치) - 승인 투표
거부 점수 = Σ(신뢰도 × 역할 가중치) - 거부 투표
비평 패널티 = Σ(심각도 가중치 × 역할 가중치) - 비평
최종 점수 = 승인 점수 - 거부 점수 - 비평 패널티
```

**판정 규칙:**
- **승인:** `점수 ≥ 임계값` AND `정족수 ≥ 정족수 비율`
- **거부권:** `veto_role`의 거부는 점수에 관계없이 제안 차단
- **필수 역할:** 모든 `required_roles`가 승인 투표해야 함

**템플릿 투표:** `.plan-executor/consensus/templates/`에서 합성 투표 템플릿을 로드하여 자동화된 게이트 판정 가능.

### 프론트스테이지 파이프라인 (멀티 에이전트 계획)

`frontstage_codex_teams.py`가 병렬 AI 에이전트를 오케스트레이션하여 실행 계획을 생성합니다:

**라운드당:**
1. **제안:** 모든 역할이 병렬로 제안 생성 (ThreadPoolExecutor)
2. **비평:** 모든 역할이 top-K 제안에 심각도 등급으로 비평
3. **수정:** 제안 작성자만 비평 기반으로 수정 + 자기 투표 (0.8 신뢰도)
4. **점수화:** 투표 집계 → 임계값 + 정족수로 제안 승인/거부

**에이전트 런타임 모드:**
- `persistent`: stdin/stdout JSONL IPC와 단계 간 메모리를 가진 장기 실행 서브프로세스 워커
- `oneshot`: 호출마다 새 서브프로세스 (대체)

**출력:** 스테이지, 합의 점수, 실행 트레이스를 포함한 프론트스테이지 계획 JSON.

### 계획 탐색 (재계획)

레인 실패 시, `plan_search.py`가 재계획 후보를 점수화합니다:

- **명령어 커버리지:** 명령어가 있는 레인 비율
- **체크포인트 커버리지:** 레인에 대한 게이트 정의
- **DAG 위험도:** 순환 감지, 도달 불가 레인
- **총점:** 가중 합산 → 최고 점수 후보 선택

`limits.max_replan`으로 제한하여 무한 루프 방지.

### 상태와 이벤트

모든 상태는 `.plan-executor/` 아래에 영속화됩니다:

```
.plan-executor/
├── events/{run_id}.jsonl    # 추가 전용 이벤트 로그
├── state/{run_id}.json      # 현재 실행 상태 스냅샷
├── messages/{run_id}.jsonl  # 에이전트 간 메시지
├── control/messages/        # 컨트롤 플레인 IPC 메시지
├── artifacts/{run_id}/      # 레인 출력 아티팩트
├── consensus/{run_id}/      # 합의 라운드 데이터
│   └── templates/           # 합성 투표 템플릿
├── delegates/               # 비동기 작업 큐
│   ├── pending/
│   ├── claimed/
│   └── completed/
├── agents/registry.json     # 스페셜리스트 레지스트리
├── worktrees/{run_id}/      # Git worktree 격리
├── runbooks/                # 런북 파일
├── team-manifests/          # 워커 매니페스트 파일
└── logs/                    # 실행 로그
```

**실행 생명주기:** `pending → running → completed | failed | aborted`

**이벤트 유형:** `preflight`, `lane_start`, `lane_done`, `checkpoint`, `replan_candidate_selected`, `consensus_reconfigured`, `message_error`

중단된 실행은 `runtime_cli.py resume --run-id <id>`로 재개 가능.

### 스페셜리스트 레지스트리

14개 기본 역할: orchestrator, integrator, planner, architect, security-reviewer, designer, frontend, backend, qa, devops-engineer, data-engineer, performance-engineer, reliability-engineer, documentation-writer.

커스텀 스페셜리스트는 `.plan-executor/agents/registry.json`에 추가 가능.

## CLI 레퍼런스

### runtime_cli.py

| 명령어 | 설명 |
|--------|------|
| start | 런북에서 새 실행 시작 |
| status | 실행 상태 및 최근 이벤트 표시 |
| resume | 일시정지 또는 중단된 실행 재개 |
| abort | 실행 중이거나 일시정지된 실행 중단 |
| runs | 모든 실행 목록 |

```bash
python scripts/runtime_cli.py --project-root . start --runbook <경로> [--manifest <경로>] [--engine codex] [--adapter ai-worker]
python scripts/runtime_cli.py --project-root . status --run-id <id> [--events 20] [--json]
python scripts/runtime_cli.py --project-root . resume --run-id <id>
python scripts/runtime_cli.py --project-root . abort --run-id <id> [--reason "..."]
python scripts/runtime_cli.py --project-root . runs
```

### runtime_daemon_cli.py

| 명령어 | 설명 |
|--------|------|
| enqueue | 데몬 큐에 런북 추가 |
| run-once | 큐에서 하나 처리 후 종료 |
| serve | 데몬 루프 지속 실행 |
| recover | 이전 세션의 중단된 실행 복구 |
| stats | 데몬 큐 및 실행 통계 표시 |

```bash
python scripts/runtime_daemon_cli.py --project-root . enqueue --runbook <경로>
python scripts/runtime_daemon_cli.py --project-root . serve
python scripts/runtime_daemon_cli.py --project-root . recover
python scripts/runtime_daemon_cli.py --project-root . stats
```

### runtime_control_cli.py

| 명령어 | 설명 |
|--------|------|
| serve | 컨트롤 서버 시작 |
| send | 실행 중인 런에 컨트롤 메시지 전송 |
| consensus-patch | 런에 합의 패치 적용 |
| enqueue | 컨트롤 서버를 통해 런북 큐잉 |
| stats | 컨트롤 서버 통계 표시 |
| list | 특정 런의 컨트롤 메시지 목록 |

```bash
python scripts/runtime_control_cli.py --project-root . serve
python scripts/runtime_control_cli.py --project-root . send --run-id <id> --kind replan --payload-json '{"reason":"..."}'
python scripts/runtime_control_cli.py --project-root . stats
```

### frontstage_codex_teams.py

```bash
python scripts/frontstage_codex_teams.py \
  --project-root . \
  --objective "인증 기능이 있는 REST API 빌드" \
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

| 플래그 | 설명 |
|--------|------|
| --run-id | 특정 런의 대시보드 표시 |
| --events N | 최근 N개 이벤트 표시 (기본값 10) |
| --json | JSON 형식 출력 |

```bash
python scripts/runtime_dashboard.py --project-root .
python scripts/runtime_dashboard.py --project-root . --run-id <id> --events 20
python scripts/runtime_dashboard.py --project-root . --json
```

## 리그레션 테스트

7개 테스트 스위트, 20개 테스트 케이스:

```bash
python scripts/guardrails_regression_test.py --project-root .         # 7 케이스
python scripts/runbook_lint_regression_test.py --project-root .       # 3 케이스
python scripts/consensus_regression_test.py --project-root .          # 2 케이스
python scripts/plan_search_regression_test.py --project-root .        # 2 케이스
python scripts/frontstage_codex_teams_regression_test.py --project-root .  # 1 케이스 (다단계)
python scripts/delegate_worker_regression_test.py --project-root .    # 1 케이스 (E2E)
python scripts/ai_worker_regression_test.py --project-root .          # 2 케이스 (codex/repair 분리)
```

## 로드맵

[ROADMAP.md](ROADMAP.md) 참조.

## 라이선스

MIT
