#!/usr/bin/env python3
"""Consensus synthetic-vote template loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def templates_root(project_root: Path) -> Path:
    return project_root.resolve() / ".plan-executor" / "consensus" / "templates"


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _normalize_vote_rows(rows: Any) -> List[Dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        author = str(row.get("author", "")).strip()
        if not author:
            continue
        decision = str(row.get("decision", "approve")).strip().lower()
        if decision not in {"approve", "reject", "abstain"}:
            continue
        role = str(row.get("role", author)).strip() or author
        confidence = _safe_float(row.get("confidence", 1.0), 1.0)
        if confidence < 0.0:
            confidence = 0.0
        if confidence > 1.0:
            confidence = 1.0
        weight = _safe_float(row.get("weight", 1.0), 1.0)
        if weight <= 0:
            weight = 1.0
        out.append(
            {
                "author": author,
                "role": role,
                "decision": decision,
                "confidence": confidence,
                "weight": weight,
            }
        )
    return out


def resolve_template_path(project_root: Path, template_ref: str) -> Path:
    ref = str(template_ref).strip()
    if not ref:
        raise ValueError("empty template_ref")

    path = Path(ref)
    if path.is_absolute():
        return path

    root = templates_root(project_root)
    if path.suffix.lower() == ".json":
        return root / path
    return root / f"{ref}.json"


def load_synthetic_votes_template(project_root: Path, template_ref: str) -> Tuple[str, List[Dict[str, Any]], str]:
    path = resolve_template_path(project_root, template_ref)
    if not path.exists():
        raise FileNotFoundError(f"template not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))

    template_id = ""
    votes: List[Dict[str, Any]] = []
    if isinstance(payload, dict):
        template_id = str(payload.get("id", "")).strip() or str(payload.get("name", "")).strip()
        votes = _normalize_vote_rows(payload.get("votes", []))
    elif isinstance(payload, list):
        votes = _normalize_vote_rows(payload)
    if not votes:
        raise ValueError(f"template has no valid votes: {path}")
    if not template_id:
        template_id = path.stem
    return template_id, votes, str(path)

