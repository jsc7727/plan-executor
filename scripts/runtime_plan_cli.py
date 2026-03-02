#!/usr/bin/env python3
"""CLI for plan-search candidate scoring."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from runtime.plan_search import score_replan_candidate, select_best_replan_candidate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="plan-executor plan-search CLI")
    parser.add_argument("--project-root", default=".", help="Project root (for future extension)")
    sub = parser.add_subparsers(dest="command", required=True)

    score = sub.add_parser("score-candidates", help="Score and rank candidate replans")
    score.add_argument("--input-json", required=True, help="Path to JSON file (list or object with candidate_plans)")
    score.add_argument("--baseline-lanes", default="", help="Comma-separated baseline lane ids")
    score.add_argument("--json", action="store_true")
    return parser.parse_args()


def _read_payload(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _baseline_from_text(text: str) -> List[str]:
    if not text.strip():
        return []
    return [x.strip() for x in text.split(",") if x.strip()]


def _extract_candidates(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(x) for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        rows = payload.get("candidate_plans", [])
        if isinstance(rows, list):
            return [dict(x) for x in rows if isinstance(x, dict)]
    return []


def main() -> int:
    args = parse_args()
    if args.command != "score-candidates":
        print("[ERROR] unsupported command")
        return 1

    path = Path(args.input_json).resolve()
    if not path.exists():
        print(f"[ERROR] missing file: {path}")
        return 1

    payload = _read_payload(path)
    candidates = _extract_candidates(payload)
    baseline = _baseline_from_text(args.baseline_lanes)
    if isinstance(payload, dict) and not baseline:
        baseline = [str(x).strip() for x in payload.get("baseline_lane_ids", []) if str(x).strip()]

    ranking = [score_replan_candidate(c, baseline) for c in candidates]
    ranking.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
    selected = select_best_replan_candidate(candidates, baseline)
    out = {
        "baseline_lane_ids": baseline,
        "selected_id": selected.get("selected_id", ""),
        "selected_score": selected.get("selected_score", 0.0),
        "ranking": [{k: v for k, v in row.items() if k != "patch"} for row in ranking],
    }

    if args.json:
        print(json.dumps(out, indent=2))
        return 0

    print(f"selected_id={out['selected_id']} selected_score={out['selected_score']}")
    for row in out["ranking"]:
        print(
            f"- id={row['candidate_id']} score={row['score']} lanes={row['lane_count']} "
            f"cmd_cov={row['commands_coverage']} cp_cov={row['checkpoints_coverage']} "
            f"unresolved={row['unresolved_dependencies']} self_dep={row['self_dependencies']} cycle={row['cycle_detected']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

