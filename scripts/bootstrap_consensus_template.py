#!/usr/bin/env python3
"""Bootstrap consensus synthetic-vote template files."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List


PRESETS: Dict[str, Dict[str, object]] = {
    "product-web-app-default": {
        "description": "Balanced approve votes for planner/designer/frontend/backend/qa.",
        "votes": [
            {"author": "planner", "role": "planner", "decision": "approve", "confidence": 0.9, "weight": 1.0},
            {"author": "designer", "role": "designer", "decision": "approve", "confidence": 0.85, "weight": 1.0},
            {"author": "frontend", "role": "frontend", "decision": "approve", "confidence": 0.9, "weight": 1.0},
            {"author": "backend", "role": "backend", "decision": "approve", "confidence": 0.9, "weight": 1.0},
            {"author": "qa", "role": "qa", "decision": "approve", "confidence": 0.95, "weight": 1.2},
        ],
    },
    "product-web-app-skeptical-qa": {
        "description": "QA-biased skeptical pattern; QA rejects unless risk is low.",
        "votes": [
            {"author": "planner", "role": "planner", "decision": "approve", "confidence": 0.8, "weight": 1.0},
            {"author": "designer", "role": "designer", "decision": "approve", "confidence": 0.8, "weight": 1.0},
            {"author": "frontend", "role": "frontend", "decision": "approve", "confidence": 0.85, "weight": 1.0},
            {"author": "backend", "role": "backend", "decision": "approve", "confidence": 0.85, "weight": 1.0},
            {"author": "qa", "role": "qa", "decision": "reject", "confidence": 0.9, "weight": 1.3},
        ],
    },
    "metagpt-swe-line-default": {
        "description": "Approve-heavy pattern for MetaGPT SWE line roles.",
        "votes": [
            {"author": "product-manager", "role": "product-manager", "decision": "approve", "confidence": 0.85, "weight": 1.0},
            {"author": "architect", "role": "architect", "decision": "approve", "confidence": 0.9, "weight": 1.1},
            {"author": "project-manager", "role": "project-manager", "decision": "approve", "confidence": 0.85, "weight": 1.0},
            {"author": "engineer", "role": "engineer", "decision": "approve", "confidence": 0.9, "weight": 1.0},
            {"author": "qa-engineer", "role": "qa-engineer", "decision": "approve", "confidence": 0.95, "weight": 1.2},
        ],
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate consensus synthetic-vote template JSON.")
    parser.add_argument(
        "--preset",
        choices=sorted(PRESETS.keys()),
        default="product-web-app-default",
        help="Template preset id.",
    )
    parser.add_argument(
        "--name",
        default="",
        help="Output template id/name. Defaults to preset id.",
    )
    parser.add_argument(
        "--project-root",
        default=".",
        help="Project root where .plan-executor artifacts are stored.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Explicit output path. Defaults to <project-root>/.plan-executor/consensus/templates/<name>.json",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite output if it already exists.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    preset = PRESETS[args.preset]
    name = str(args.name).strip() or args.preset
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    project_root = Path(args.project_root).resolve()
    output = (
        Path(args.output).resolve()
        if args.output
        else project_root / ".plan-executor" / "consensus" / "templates" / f"{name}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not args.force:
        print(f"[ERROR] output exists: {output} (use --force)")
        return 1

    votes: List[Dict[str, object]] = [dict(v) for v in preset["votes"]]  # type: ignore[index]
    payload = {
        "id": name,
        "preset": args.preset,
        "description": str(preset["description"]),
        "created_at_utc": now,
        "votes": votes,
    }
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    print(f"[OK] template written: {output}")
    print(f"[OK] preset={args.preset} votes={len(votes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

