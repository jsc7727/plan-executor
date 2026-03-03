#!/usr/bin/env python3
"""E2E regression tests for ai-worker (codex) runtime behavior."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from runtime.event_store import EventStore
from runtime.orchestrator import RuntimeOrchestrator
from runtime.worker_adapters import AiCliWorkerAdapter
import runtime.worker_adapters as worker_adapters_module


def utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _shell_command_template() -> str:
    is_windows = platform.system().strip().lower().startswith("win")
    return "cmd /c {cmd}" if is_windows else "{cmd}"


def build_runbook(path: Path, limits: Dict[str, Any] | None = None) -> None:
    payload = {
        "meta": {
            "generated_at_utc": utc_compact(),
            "preset": "product-web-app",
            "profile": "speed",
            "mode": "sequential",
            "task_type": "code",
            "max_parallel_workers": 1,
        },
        "dag": {
            "nodes": [
                {"id": "lane-1", "depends_on": []},
                {"id": "lane-2", "depends_on": ["lane-1"]},
            ]
        },
        "lanes": [
            {
                "id": "lane-1",
                "owner_role": "planner",
                "commands": ['echo {"lane":"lane-1","ok":true}'],
                "done_criteria": ["checkpoint accepted by integrator"],
            },
            {
                "id": "lane-2",
                "owner_role": "designer",
                "commands": ['echo {"lane":"lane-2","ok":true}'],
                "done_criteria": ["checkpoint accepted by integrator"],
            },
        ],
        "checkpoints": [
            {
                "id": "checkpoint-1",
                "after_lanes": ["lane-1", "lane-2"],
                "gate_criteria": ["targeted-tests-pass"],
                "gate_commands": [],
            }
        ],
        "limits": {
            "max_replan": 3,
            "stall_rounds_threshold": 2,
            "merge_conflicts_threshold": 2,
            "verification_pass_rate_min": 0.7,
            "ai_worker_skip_warn_streak": 2,
            "ai_worker_skip_fail_streak": 0,
        },
    }
    if limits:
        payload["limits"].update(limits)
    write_json(path, payload)


def build_manifest(path: Path, engines: List[str]) -> None:
    cmd_template = _shell_command_template()
    workers = []
    roles = ["planner", "designer"]
    ids = ["worker-1", "worker-2"]
    for idx, engine in enumerate(engines):
        workers.append(
            {
                "id": ids[idx],
                "role": roles[idx],
                "stage": "build",
                "contract": f"{roles[idx]}-artifact",
                "engine": engine,
                "command_template": cmd_template,
                "timeout_sec": 30,
                "max_retries": 1,
                "backoff_sec": 0.5,
            }
        )
    payload = {
        "meta": {
            "generated_at_utc": utc_compact(),
            "mode": "swarm-style",
            "adapter": "ai-worker",
            "requested_adapter": "ai-worker",
            "task_type": "code",
            "worker_count": len(workers),
            "ai_engine": engines[0] if engines else "codex",
        },
        "workers": workers,
    }
    write_json(path, payload)


def collect_ai_lane_metrics(store: EventStore, run_id: str) -> Dict[str, Any]:
    events = store.read_events(run_id, limit=0)
    lane_done = [e for e in events if str(e.get("event")) == "lane_done"]
    skip = 0
    by_engine: Dict[str, int] = {}
    for evt in lane_done:
        payload = evt.get("payload", {})
        evidence = [str(x).strip().lower() for x in payload.get("evidence", [])]
        engine = "unknown"
        for token in evidence:
            if token.startswith("engine:"):
                engine = token.split(":", 1)[1].strip()
                break
        by_engine[engine] = by_engine.get(engine, 0) + 1
        if "ai-worker-unavailable-skip" in evidence:
            skip += 1
    return {
        "lane_done_count": len(lane_done),
        "skip_count": skip,
        "by_engine": by_engine,
    }


def run_case(
    project_root: Path,
    case_id: str,
    engines: List[str],
) -> Tuple[bool, Dict[str, Any]]:
    pe_root = project_root / ".plan-executor"
    runbook = pe_root / "runbooks" / f"{case_id}-runbook.json"
    manifest = pe_root / "team-manifests" / f"{case_id}-manifest.json"
    run_id = f"{case_id}-{utc_compact()}"

    build_runbook(runbook)
    build_manifest(manifest, engines=engines)

    orch = RuntimeOrchestrator(project_root)
    state = orch.start(runbook_path=runbook, manifest_path=manifest, adapter_name="auto", run_id=run_id)

    store = EventStore(project_root)
    metrics = collect_ai_lane_metrics(store, run_id)
    status = str(state.get("status", "unknown"))

    ok = status == "completed"
    # Codex-only must route to codex engine.
    if case_id == "codex-only":
        ok = ok and metrics["by_engine"].get("codex", 0) == 2

    detail = {
        "case_id": case_id,
        "run_id": run_id,
        "status": status,
        **metrics,
    }
    return ok, detail


def run_repair_engine_split_case(project_root: Path) -> Tuple[bool, Dict[str, Any]]:
    adapter = AiCliWorkerAdapter()
    lane = {
        "id": "lane-repair-split",
        "owner_role": "planner",
        "commands": ["bootstrap-cmd", "failing-command"],
        "_runtime": {
            "run_id": f"repair-split-{utc_compact()}",
            "worker_id": "worker-repair",
            "worker_role": "planner",
            "worker_engine": "codex",
            "worker_command_template": 'codex exec --enable multi_agent --skip-git-repo-check "{cmd}"',
            "fallback_chain": "codex,shell",
            "ai_timeout_sec": 30,
            "ai_max_retries": 0,
            "ai_backoff_sec": 0,
            "max_replan": 1,
        },
    }

    calls: List[str] = []

    def fake_check_available(engine: str, _project_root: Path) -> Tuple[bool, str, Dict[str, Any]]:
        if engine == "codex":
            return True, "ok", {"check_cmd": "fake-codex-check", "returncode": 0, "stdout": "ok", "stderr": ""}
        return False, "unsupported-engine", {"check_cmd": "fake-engine-check", "returncode": 2, "stdout": "", "stderr": engine}

    def fake_run(cmd: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        rendered = str(cmd)
        calls.append(rendered)
        if "The following command failed" in rendered:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="repair-ok", stderr="")
        if rendered.strip() == "bootstrap-cmd":
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="bootstrap-ok", stderr="")
        if rendered.strip() == "failing-command":
            return subprocess.CompletedProcess(args=cmd, returncode=2, stdout="", stderr="tests failed")
        if "codex exec" in rendered and "bootstrap-cmd" in rendered:
            return subprocess.CompletedProcess(args=cmd, returncode=124, stdout="", stderr="timeout>30s")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    original_check_available = adapter._check_available
    original_run = worker_adapters_module.subprocess.run
    adapter._check_available = fake_check_available  # type: ignore[assignment]
    worker_adapters_module.subprocess.run = fake_run  # type: ignore[assignment]
    try:
        result = adapter.run_lane(lane, project_root)
    finally:
        adapter._check_available = original_check_available  # type: ignore[assignment]
        worker_adapters_module.subprocess.run = original_run  # type: ignore[assignment]

    repair_attempts = [row for row in result.commands if str(row.get("attempt", "")).startswith("repair-")]
    shell_fallbacks = [row for row in result.commands if str(row.get("attempt", "")) == "infra-fallback-shell"]
    repair_wrapped = str(repair_attempts[0].get("wrapped_cmd", "")) if repair_attempts else ""

    ok = (
        result.status == "pass"
        and len(repair_attempts) == 1
        and len(shell_fallbacks) == 1
        and str(repair_attempts[0].get("engine", "")) == "codex"
        and "codex exec" in repair_wrapped
        and "The following command failed" in repair_wrapped
    )
    detail = {
        "case_id": "repair-engine-split",
        "status": result.status,
        "repair_attempts": len(repair_attempts),
        "shell_fallbacks": len(shell_fallbacks),
        "repair_engine": str(repair_attempts[0].get("engine", "")) if repair_attempts else "",
        "calls": len(calls),
    }
    return ok, detail


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ai-worker E2E regression scenarios.")
    parser.add_argument("--project-root", default=".", help="Project root containing .plan-executor")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    cases = [
        ("codex-only", ["codex", "codex"]),
    ]

    passed = 0
    rows = []
    print("AI Worker Regression Test")
    print("=" * 40)
    for case_id, engines in cases:
        ok, detail = run_case(project_root, case_id, engines)
        rows.append(detail)
        status = "PASS" if ok else "FAIL"
        print(
            f"[{status}] {case_id} "
            f"run={detail['run_id']} status={detail['status']} "
            f"lane_done={detail['lane_done_count']} skip={detail['skip_count']} engines={detail['by_engine']}"
        )
        if ok:
            passed += 1

    repair_ok, repair_detail = run_repair_engine_split_case(project_root)
    rows.append(repair_detail)
    repair_status = "PASS" if repair_ok else "FAIL"
    print(
        f"[{repair_status}] {repair_detail['case_id']} "
        f"status={repair_detail['status']} repair_attempts={repair_detail['repair_attempts']} "
        f"shell_fallbacks={repair_detail['shell_fallbacks']} repair_engine={repair_detail['repair_engine']}"
    )
    if repair_ok:
        passed += 1

    total = len(cases) + 1
    print("-" * 40)
    print(f"RESULT: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
