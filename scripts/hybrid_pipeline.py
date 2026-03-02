#!/usr/bin/env python3
"""Hybrid frontstage planner -> PE execution bridge CLI."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from runtime.orchestrator import RuntimeOrchestrator
from runtime.runbook_lint import lint_runbook_payload


def utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def safe_slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or "lane"


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def try_parse_json_text(text: str) -> Dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def extract_commands(items: List[Any]) -> List[str]:
    out: List[str] = []
    for item in items:
        if isinstance(item, str):
            # Keep only explicit shell-like text; non-command text becomes scope.
            if any(ch in item for ch in [" ", "/", "\\", ".", ":", "|", "&", ";"]):
                out.append(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        for key in ("command", "cmd", "shell"):
            value = str(item.get(key, "")).strip()
            if value:
                out.append(value)
                break
    return [x for x in out if x]


def build_lanes_from_stages(stages: List[Any], objective: str) -> List[Dict[str, Any]]:
    lanes: List[Dict[str, Any]] = []
    for i, stage in enumerate(stages, start=1):
        if isinstance(stage, str):
            name = stage
            role = safe_slug(stage)
            tasks: List[Any] = []
            summary = ""
        elif isinstance(stage, dict):
            name = str(stage.get("name", f"stage-{i}"))
            role = str(stage.get("owner_role", "")).strip() or str(stage.get("role", "")).strip() or safe_slug(name)
            tasks = list(stage.get("tasks", []))
            commands = list(stage.get("commands", []))
            if commands:
                tasks.extend(commands)
            summary = str(stage.get("summary", "")).strip()
        else:
            name = f"stage-{i}"
            role = f"role-{i}"
            tasks = []
            summary = ""

        lane_id = f"lane-{i}"
        shell_cmds = extract_commands(tasks)
        scope = summary or f"{objective}: {name}"
        lanes.append(
            {
                "id": lane_id,
                "owner_role": role or f"role-{i}",
                "scope": scope,
                "input_artifacts": [],
                "output_contract": {"files_changed": [], "acceptance": []},
                "done_criteria": ["checkpoint accepted by integrator"],
                "commands": shell_cmds,
            }
        )
    return lanes


def build_lanes_from_tasks(tasks: List[Any], objective: str) -> List[Dict[str, Any]]:
    lanes: List[Dict[str, Any]] = []
    for i, task in enumerate(tasks, start=1):
        if isinstance(task, str):
            title = task
            role = f"role-{i}"
            commands = extract_commands([task])
        elif isinstance(task, dict):
            title = str(task.get("title", "")).strip() or str(task.get("name", "")).strip() or f"task-{i}"
            role = str(task.get("owner_role", "")).strip() or str(task.get("role", "")).strip() or f"role-{i}"
            commands = extract_commands([task, *list(task.get("steps", []))])
        else:
            title = f"task-{i}"
            role = f"role-{i}"
            commands = []
        lanes.append(
            {
                "id": f"lane-{i}",
                "owner_role": role,
                "scope": f"{objective}: {title}",
                "input_artifacts": [],
                "output_contract": {"files_changed": [], "acceptance": []},
                "done_criteria": ["checkpoint accepted by integrator"],
                "commands": commands,
            }
        )
    return lanes


def sequential_dag(lane_ids: List[str]) -> Dict[str, Any]:
    nodes = []
    for i, lane_id in enumerate(lane_ids):
        depends = [lane_ids[i - 1]] if i > 0 else []
        nodes.append({"id": lane_id, "depends_on": depends})
    return {"nodes": nodes}


def normalize_runbook(payload: Dict[str, Any], objective: str, source: str) -> Dict[str, Any]:
    if isinstance(payload.get("lanes"), list):
        lanes = []
        for i, lane in enumerate(payload.get("lanes", []), start=1):
            if not isinstance(lane, dict):
                continue
            lane_id = str(lane.get("id", "")).strip() or f"lane-{i}"
            owner_role = str(lane.get("owner_role", "")).strip() or str(lane.get("role", "")).strip() or f"role-{i}"
            commands = extract_commands(list(lane.get("commands", [])))
            lanes.append(
                {
                    "id": lane_id,
                    "owner_role": owner_role,
                    "scope": str(lane.get("scope", "")).strip() or f"{objective}: {lane_id}",
                    "input_artifacts": list(lane.get("input_artifacts", [])),
                    "output_contract": lane.get("output_contract", {"files_changed": [], "acceptance": []}),
                    "done_criteria": list(lane.get("done_criteria", ["checkpoint accepted by integrator"])),
                    "commands": commands,
                }
            )
    elif isinstance(payload.get("stages"), list):
        lanes = build_lanes_from_stages(list(payload.get("stages", [])), objective=objective)
    elif isinstance(payload.get("tasks"), list):
        lanes = build_lanes_from_tasks(list(payload.get("tasks", [])), objective=objective)
    else:
        lanes = [
            {
                "id": "lane-1",
                "owner_role": "planner",
                "scope": objective,
                "input_artifacts": [],
                "output_contract": {"files_changed": [], "acceptance": []},
                "done_criteria": ["checkpoint accepted by integrator"],
                "commands": [],
            }
        ]

    lane_ids = [str(x.get("id")) for x in lanes]
    dag = payload.get("dag", sequential_dag(lane_ids))
    if not isinstance(dag, dict) or not isinstance(dag.get("nodes"), list):
        dag = sequential_dag(lane_ids)

    limits_src = payload.get("limits", {}) if isinstance(payload.get("limits"), dict) else {}
    guardrails_src = limits_src.get("command_guardrails", {})
    env_src = str(payload.get("meta", {}).get("environment", "")).strip().lower() if isinstance(payload.get("meta", {}), dict) else ""
    guardrails = {
        "enabled": True,
        "profile": "ci",
        "environment": env_src or "ci",
        "os_template": "auto",
        "phases": ["lane", "gate"],
    }
    if isinstance(guardrails_src, dict):
        guardrails.update(dict(guardrails_src))
    limits = {
        "max_replan": int(limits_src.get("max_replan", 3)),
        "stall_rounds_threshold": int(limits_src.get("stall_rounds_threshold", 2)),
        "merge_conflicts_threshold": int(limits_src.get("merge_conflicts_threshold", 2)),
        "verification_pass_rate_min": float(limits_src.get("verification_pass_rate_min", 0.7)),
        "ai_worker_skip_warn_streak": int(limits_src.get("ai_worker_skip_warn_streak", 2)),
        "ai_worker_skip_fail_streak": int(limits_src.get("ai_worker_skip_fail_streak", 0)),
        "command_guardrails": guardrails,
    }

    return {
        "meta": {
            "generated_at_utc": utc_compact(),
            "preset": "hybrid-frontstage-bridge",
            "paper_basis": ["MetaGPT", "ChatDev", "AutoGen"],
            "profile": "balanced",
            "mode": "parallel" if len(lane_ids) > 1 else "sequential",
            "task_type": "code",
            "max_parallel_workers": max(1, min(4, len(lane_ids))),
            "source": source,
            "objective": objective,
        },
        "team": {
            "orchestrator": "enabled",
            "integrator": "enabled",
            "lane_roles": [str(l.get("owner_role", "unassigned")) for l in lanes],
        },
        "dag": dag,
        "lanes": lanes,
        "checkpoints": [
            {
                "id": "checkpoint-1",
                "after_lanes": lane_ids,
                "gate_criteria": ["targeted-tests-pass"],
                "gate_commands": [],
            }
        ],
        "limits": limits,
        "hooks": ["preflight", "lane_start", "lane_done", "checkpoint", "post_merge", "finalize"],
    }


def build_manifest(runbook: Dict[str, Any], adapter: str, ai_engine: str) -> Dict[str, Any]:
    lanes = list(runbook.get("lanes", []))
    roles: List[str] = []
    for lane in lanes:
        role = str(lane.get("owner_role", "")).strip()
        if role and role not in roles:
            roles.append(role)
    if not roles:
        roles = ["planner"]

    workers: List[Dict[str, Any]] = []
    for i, role in enumerate(roles, start=1):
        engine = ""
        if adapter == "ai-worker":
            if ai_engine == "mixed":
                engine = "codex" if (i % 2 == 1) else "gemini"
            else:
                engine = ai_engine
        if adapter == "ai-worker":
            command_template = 'codex exec --skip-git-repo-check "{cmd}"' if engine != "gemini" else 'gemini -p "{cmd}" --yolo'
        elif adapter == "process-worker":
            command_template = "{cmd}"
        else:
            command_template = ""

        workers.append(
            {
                "id": f"worker-{i}",
                "role": role,
                "stage": "build",
                "contract": f"{safe_slug(role)}-artifact",
                "engine": engine,
                "command_template": command_template,
                "timeout_sec": 180,
                "max_retries": 1,
                "backoff_sec": 1.5,
            }
        )

    return {
        "meta": {
            "generated_at_utc": utc_compact(),
            "mode": "hybrid-frontstage",
            "adapter": adapter,
            "requested_adapter": adapter,
            "task_type": "code",
            "worker_count": len(workers),
            "ai_engine": ai_engine if adapter == "ai-worker" else "",
        },
        "workers": workers,
        "hooks": ["preflight", "lane_start", "lane_done", "checkpoint", "post_merge", "finalize"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hybrid frontstage planner to PE execution bridge")
    parser.add_argument("--project-root", default=".", help="Project root containing .plan-executor")
    parser.add_argument("--objective", default="Hybrid pipeline execution", help="Execution objective label")
    parser.add_argument("--planner-cmd", default="", help="Optional external planner command that outputs JSON plan")
    parser.add_argument("--frontstage-plan", default="", help="Path to frontstage JSON plan artifact")
    parser.add_argument("--omc-plan", default="", help=argparse.SUPPRESS)
    parser.add_argument("--runbook-out", default="", help="Output runbook JSON path")
    parser.add_argument("--manifest-out", default="", help="Output manifest JSON path")
    parser.add_argument("--adapter", default="auto", choices=["auto", "inline-worker", "process-worker", "ai-worker", "worktree-worker", "tmux-worker"])
    parser.add_argument("--ai-engine", default="codex", choices=["codex", "gemini", "mixed"])
    parser.add_argument("--run-id", default="", help="Optional explicit run id")
    parser.add_argument("--prepare-only", action="store_true", help="Prepare artifacts only without starting run")
    parser.add_argument(
        "--skip-runbook-lint",
        action="store_true",
        help="Skip strict runbook lint check after runbook normalization.",
    )
    return parser.parse_args()


def run_planner_command(cmd: str, cwd: Path) -> Dict[str, Any]:
    proc = subprocess.run(cmd, shell=True, cwd=str(cwd), capture_output=True, text=True)
    payload = try_parse_json_text(proc.stdout)
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-4000:],
        "stderr": (proc.stderr or "")[-4000:],
        "payload": payload,
    }


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    pe_root = project_root / ".plan-executor"
    now = utc_compact()
    runbook_path = Path(args.runbook_out).resolve() if args.runbook_out else pe_root / "runbooks" / f"hybrid-runbook-{now}.json"
    manifest_path = Path(args.manifest_out).resolve() if args.manifest_out else pe_root / "team-manifests" / f"hybrid-manifest-{now}.json"

    planner_result: Dict[str, Any] | None = None
    source = "fallback"
    source_payload: Dict[str, Any] | None = None

    if args.planner_cmd.strip():
        planner_result = run_planner_command(args.planner_cmd.strip(), cwd=project_root)
        if planner_result["returncode"] == 0 and isinstance(planner_result.get("payload"), dict):
            source = "planner-cmd-stdout"
            source_payload = dict(planner_result["payload"])
        else:
            print(f"[WARN] planner command failed or non-JSON output. rc={planner_result['returncode']}")

    plan_arg = args.frontstage_plan.strip() or args.omc_plan.strip()
    if source_payload is None and plan_arg:
        plan_path = Path(plan_arg).resolve()
        if not plan_path.exists():
            print(f"[ERROR] frontstage plan not found: {plan_path}")
            return 2
        source = "frontstage-plan-file"
        source_payload = read_json(plan_path)

    if source_payload is None:
        source = "fallback-default"
        source_payload = {
            "stages": [
                {"name": "plan", "owner_role": "planner", "tasks": []},
                {"name": "build", "owner_role": "frontend", "tasks": []},
                {"name": "verify", "owner_role": "qa", "tasks": []},
            ]
        }
        print("[WARN] no planner output/plan file provided; using default hybrid skeleton.")

    runbook = normalize_runbook(source_payload, objective=args.objective, source=source)
    if not bool(args.skip_runbook_lint):
        lint = lint_runbook_payload(runbook, strict=True)
        if not bool(lint.get("ok", False)):
            print("[ERROR] runbook lint failed after normalization")
            for row in lint.get("errors", []):
                if not isinstance(row, dict):
                    continue
                print(
                    f"  - {row.get('code', 'lint-error')} "
                    f"path={row.get('path', '')} message={row.get('message', '')}"
                )
            return 2
        for row in lint.get("warnings", []):
            if not isinstance(row, dict):
                continue
            print(
                f"[WARN] runbook lint: {row.get('code', 'lint-warning')} "
                f"path={row.get('path', '')} message={row.get('message', '')}"
            )
    adapter = args.adapter if args.adapter != "auto" else "process-worker"
    manifest = build_manifest(runbook, adapter=adapter, ai_engine=args.ai_engine)
    write_json(runbook_path, runbook)
    write_json(manifest_path, manifest)

    run_id = args.run_id.strip() or f"hybrid-{now}-{uuid.uuid4().hex[:6]}"
    bridge_log = {
        "ts": utc_now(),
        "project_root": str(project_root),
        "objective": args.objective,
        "source": source,
        "planner_result": planner_result or {},
        "runbook_path": str(runbook_path),
        "manifest_path": str(manifest_path),
        "adapter": adapter,
        "ai_engine": args.ai_engine if adapter == "ai-worker" else "",
        "prepare_only": bool(args.prepare_only),
        "run_id": run_id,
    }
    bridge_log_path = pe_root / "logs" / f"hybrid-bridge-{run_id}.json"
    write_json(bridge_log_path, bridge_log)

    print(f"[OK] runbook={runbook_path}")
    print(f"[OK] manifest={manifest_path}")
    print(f"[OK] bridge_log={bridge_log_path}")

    if args.prepare_only:
        print("[OK] prepare-only complete")
        return 0

    try:
        orch = RuntimeOrchestrator(project_root)
        state = orch.start(
            runbook_path=runbook_path,
            manifest_path=manifest_path,
            adapter_name=adapter,
            run_id=run_id,
        )
        print(f"[OK] run_id={state.get('run_id')} status={state.get('status')} adapter={state.get('adapter')}")
        return 0 if state.get("status") == "completed" else 1
    except Exception as exc:
        print(f"[ERROR] hybrid run failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
