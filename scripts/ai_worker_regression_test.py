#!/usr/bin/env python3
"""E2E regression tests for ai-worker (codex/gemini) runtime behavior."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from runtime.event_store import EventStore
from runtime.orchestrator import RuntimeOrchestrator


def utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


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
                # Avoid real model invocation in regression tests.
                "command_template": "cmd /c {cmd}",
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
            "ai_engine": "mixed" if len(set(engines)) > 1 else engines[0],
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
    force_empty_gemini_key: bool,
) -> Tuple[bool, Dict[str, Any]]:
    pe_root = project_root / ".plan-executor"
    runbook = pe_root / "runbooks" / f"{case_id}-runbook.json"
    manifest = pe_root / "team-manifests" / f"{case_id}-manifest.json"
    run_id = f"{case_id}-{utc_compact()}"

    build_runbook(runbook)
    build_manifest(manifest, engines=engines)

    old_key = os.environ.get("GEMINI_API_KEY")
    try:
        if force_empty_gemini_key:
            os.environ["GEMINI_API_KEY"] = ""
        orch = RuntimeOrchestrator(project_root)
        state = orch.start(runbook_path=runbook, manifest_path=manifest, adapter_name="auto", run_id=run_id)
    finally:
        if force_empty_gemini_key:
            if old_key is None:
                os.environ.pop("GEMINI_API_KEY", None)
            else:
                os.environ["GEMINI_API_KEY"] = old_key

    store = EventStore(project_root)
    metrics = collect_ai_lane_metrics(store, run_id)
    status = str(state.get("status", "unknown"))

    ok = status == "completed"
    # Gemini-missing-key scenario must skip both lanes.
    if case_id == "gemini-missing-key":
        ok = ok and metrics["skip_count"] == 2 and metrics["by_engine"].get("gemini", 0) == 2
    # Gemini-explicit must route to gemini engine.
    if case_id == "gemini-explicit":
        ok = ok and metrics["by_engine"].get("gemini", 0) == 2
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ai-worker E2E regression scenarios.")
    parser.add_argument("--project-root", default=".", help="Project root containing .plan-executor")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    cases = [
        ("codex-only", ["codex", "codex"], False),
        ("gemini-explicit", ["gemini", "gemini"], False),
        ("gemini-missing-key", ["gemini", "gemini"], True),
    ]

    passed = 0
    rows = []
    print("AI Worker Regression Test")
    print("=" * 40)
    for case_id, engines, force_empty in cases:
        ok, detail = run_case(project_root, case_id, engines, force_empty)
        rows.append(detail)
        status = "PASS" if ok else "FAIL"
        print(
            f"[{status}] {case_id} "
            f"run={detail['run_id']} status={detail['status']} "
            f"lane_done={detail['lane_done_count']} skip={detail['skip_count']} engines={detail['by_engine']}"
        )
        if ok:
            passed += 1

    total = len(cases)
    print("-" * 40)
    print(f"RESULT: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

