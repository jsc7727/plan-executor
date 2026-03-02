#!/usr/bin/env python3
"""Structured consensus protocol (RALPLAN-DR-like) for plan-executor."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def consensus_root(project_root: Path) -> Path:
    return project_root.resolve() / ".plan-executor" / "consensus"


def round_path(project_root: Path, run_id: str, round_id: str) -> Path:
    return consensus_root(project_root) / run_id / f"{round_id}.json"


@dataclass
class ConsensusRound:
    run_id: str
    round_id: str
    topic: str
    participants: List[str]
    created_at: str
    status: str
    policy: Dict[str, object]
    proposals: List[Dict[str, object]]


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "y", "on"}:
        return True
    if token in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _clean_tokens(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    out: List[str] = []
    for item in values:
        token = str(item).strip()
        if token:
            out.append(token)
    return out


def _normalize_role_weights(value: Any) -> Dict[str, float]:
    if not isinstance(value, dict):
        return {}
    out: Dict[str, float] = {}
    for raw_key, raw_val in value.items():
        role = str(raw_key).strip().lower()
        if not role:
            continue
        w = _safe_float(raw_val, 1.0)
        if w < 0.1:
            w = 0.1
        if w > 5.0:
            w = 5.0
        out[role] = round(w, 4)
    return out


def _normalize_policy(policy: Dict[str, Any] | None = None) -> Dict[str, object]:
    policy = dict(policy or {})
    threshold = _safe_float(policy.get("threshold", 0.67), 0.67)
    if threshold < 0.5:
        threshold = 0.5
    if threshold > 1.0:
        threshold = 1.0

    quorum_ratio = _safe_float(policy.get("quorum_ratio", 0.6), 0.6)
    if quorum_ratio < 0.1:
        quorum_ratio = 0.1
    if quorum_ratio > 1.0:
        quorum_ratio = 1.0

    reject_threshold = _safe_float(policy.get("reject_threshold", threshold), threshold)
    if reject_threshold < 0.5:
        reject_threshold = 0.5
    if reject_threshold > 1.0:
        reject_threshold = 1.0

    min_critiques = _safe_int(policy.get("min_critiques", 0), 0)
    if min_critiques < 0:
        min_critiques = 0

    min_approve_confidence = _safe_float(policy.get("min_approve_confidence", 0.0), 0.0)
    if min_approve_confidence < 0.0:
        min_approve_confidence = 0.0
    if min_approve_confidence > 1.0:
        min_approve_confidence = 1.0

    veto_roles = _clean_tokens(policy.get("veto_roles", []))
    required_roles = _clean_tokens(policy.get("required_roles", []))
    allow_abstain = _safe_bool(policy.get("allow_abstain", True), True)
    single_winner = _safe_bool(policy.get("single_winner", False), False)
    role_weights = _normalize_role_weights(policy.get("role_weights", {}))
    return {
        "threshold": threshold,
        "reject_threshold": reject_threshold,
        "quorum_ratio": quorum_ratio,
        "min_critiques": min_critiques,
        "min_approve_confidence": min_approve_confidence,
        "veto_roles": veto_roles,
        "required_roles": required_roles,
        "allow_abstain": allow_abstain,
        "single_winner": single_winner,
        "role_weights": role_weights,
    }


def _load_round(path: Path) -> ConsensusRound:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return ConsensusRound(
        run_id=str(payload["run_id"]),
        round_id=str(payload["round_id"]),
        topic=str(payload["topic"]),
        participants=[str(x) for x in payload.get("participants", [])],
        created_at=str(payload.get("created_at", "")),
        status=str(payload.get("status", "open")),
        policy=_normalize_policy(payload.get("policy", {})),
        proposals=[dict(x) for x in payload.get("proposals", [])],
    )


def _save_round(project_root: Path, rnd: ConsensusRound) -> None:
    path = round_path(project_root, rnd.run_id, rnd.round_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": rnd.run_id,
        "round_id": rnd.round_id,
        "topic": rnd.topic,
        "participants": rnd.participants,
        "created_at": rnd.created_at,
        "status": rnd.status,
        "policy": rnd.policy,
        "proposals": rnd.proposals,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def create_round(
    project_root: Path,
    run_id: str,
    topic: str,
    participants: List[str],
    threshold: float = 0.67,
    quorum_ratio: float = 0.6,
    reject_threshold: float | None = None,
    veto_roles: List[str] | None = None,
    required_roles: List[str] | None = None,
    min_critiques: int = 0,
    allow_abstain: bool = True,
    role_weights: Dict[str, float] | None = None,
    min_approve_confidence: float = 0.0,
    single_winner: bool = False,
) -> ConsensusRound:
    round_id = f"round-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    rnd = ConsensusRound(
        run_id=run_id,
        round_id=round_id,
        topic=topic,
        participants=[p.strip() for p in participants if p.strip()],
        created_at=utc_now(),
        status="open",
        policy=_normalize_policy(
            {
                "threshold": threshold,
                "reject_threshold": reject_threshold if reject_threshold is not None else threshold,
                "quorum_ratio": quorum_ratio,
                "veto_roles": list(veto_roles or []),
                "required_roles": list(required_roles or []),
                "min_critiques": min_critiques,
                "allow_abstain": allow_abstain,
                "role_weights": dict(role_weights or {}),
                "min_approve_confidence": min_approve_confidence,
                "single_winner": single_winner,
            }
        ),
        proposals=[],
    )
    _save_round(project_root, rnd)
    return rnd


def load_round(project_root: Path, run_id: str, round_id: str) -> ConsensusRound:
    path = round_path(project_root, run_id, round_id)
    if not path.exists():
        raise FileNotFoundError(f"round not found: {path}")
    return _load_round(path)


def submit_proposal(
    project_root: Path,
    run_id: str,
    round_id: str,
    author: str,
    content: str,
    rationale: str = "",
    risks: List[str] | None = None,
) -> Dict[str, object]:
    rnd = load_round(project_root, run_id, round_id)
    proposal_id = f"prop-{uuid.uuid4().hex[:6]}"
    proposal = {
        "proposal_id": proposal_id,
        "author": author,
        "content": content,
        "rationale": rationale,
        "risks": [str(x) for x in (risks or []) if str(x).strip()],
        "created_at": utc_now(),
        "critiques": [],
        "votes": [],
        "decision": "pending",
    }
    rnd.proposals.append(proposal)
    _save_round(project_root, rnd)
    return proposal


def submit_critique(
    project_root: Path,
    run_id: str,
    round_id: str,
    proposal_id: str,
    author: str,
    content: str,
    severity: str = "medium",
    evidence: List[str] | None = None,
) -> Dict[str, object]:
    rnd = load_round(project_root, run_id, round_id)
    for p in rnd.proposals:
        if str(p.get("proposal_id")) == proposal_id:
            critique = {
                "author": author,
                "content": content,
                "severity": str(severity).strip() or "medium",
                "evidence": [str(x) for x in (evidence or []) if str(x).strip()],
                "created_at": utc_now(),
            }
            p.setdefault("critiques", []).append(critique)
            _save_round(project_root, rnd)
            return critique
    raise ValueError(f"proposal not found: {proposal_id}")


def vote(
    project_root: Path,
    run_id: str,
    round_id: str,
    proposal_id: str,
    author: str,
    decision: str,
    confidence: float = 1.0,
    weight: float = 1.0,
    role: str = "",
) -> Dict[str, object]:
    decision = decision.strip().lower()
    if decision not in {"approve", "reject", "abstain"}:
        raise ValueError("decision must be approve|reject|abstain")
    confidence = _safe_float(confidence, 1.0)
    if confidence < 0.0:
        confidence = 0.0
    if confidence > 1.0:
        confidence = 1.0
    weight = _safe_float(weight, 1.0)
    if weight <= 0:
        weight = 1.0
    rnd = load_round(project_root, run_id, round_id)
    for p in rnd.proposals:
        if str(p.get("proposal_id")) == proposal_id:
            votes = [v for v in p.setdefault("votes", []) if str(v.get("author")) != author]
            votes.append(
                {
                    "author": author,
                    "role": (role.strip() or author.strip()),
                    "decision": decision,
                    "confidence": confidence,
                    "weight": weight,
                    "created_at": utc_now(),
                }
            )
            p["votes"] = votes
            _save_round(project_root, rnd)
            return {"proposal_id": proposal_id, "votes": votes}
    raise ValueError(f"proposal not found: {proposal_id}")


def _proposal_stats(
    proposal: Dict[str, object],
    participants: List[str],
    threshold: float,
    reject_threshold: float,
    quorum_ratio: float,
    veto_roles: List[str],
    required_roles: List[str],
    min_critiques: int,
    allow_abstain: bool,
    role_weights: Dict[str, float],
    min_approve_confidence: float,
) -> Dict[str, object]:
    votes: List[Dict[str, Any]] = [dict(v) for v in proposal.get("votes", [])]
    critiques: List[Dict[str, Any]] = [dict(x) for x in proposal.get("critiques", [])]
    unique_voters = sorted({str(v.get("author", "")).strip() for v in votes if str(v.get("author", "")).strip()})
    participant_roles = [str(x).strip().lower() for x in participants if str(x).strip()]
    if participant_roles:
        total_participants = len(participant_roles)
        participant_weight_total = sum(float(role_weights.get(role, 1.0)) for role in participant_roles)
    else:
        total_participants = 1
        participant_weight_total = 1.0
    if participant_weight_total <= 0:
        participant_weight_total = float(total_participants)

    voted_role_by_author: Dict[str, str] = {}
    for v in votes:
        author = str(v.get("author", "")).strip()
        if not author:
            continue
        role = str(v.get("role", "")).strip().lower() or author.lower()
        voted_role_by_author[author] = role

    quorum_weight = 0.0
    for role in voted_role_by_author.values():
        quorum_weight += float(role_weights.get(role, 1.0))
    quorum_met = (quorum_weight / participant_weight_total) >= quorum_ratio
    critiques_ok = len(critiques) >= min_critiques

    approve_weight = 0.0
    reject_weight = 0.0
    abstain_weight = 0.0
    approve_conf_sum = 0.0
    approve_count = 0
    veto_hit = False
    veto_by: List[str] = []
    veto_set = {str(x).strip().lower() for x in veto_roles if str(x).strip()}
    required_set = {str(x).strip().lower() for x in required_roles if str(x).strip()}
    voted_required: set[str] = set()
    abstain_count = 0

    for v in votes:
        decision = str(v.get("decision", "")).strip().lower()
        author = str(v.get("author", "")).strip()
        role = str(v.get("role", "")).strip().lower() or author.lower()
        conf = _safe_float(v.get("confidence", 1.0), 1.0)
        if conf < 0.0:
            conf = 0.0
        if conf > 1.0:
            conf = 1.0
        base_w = _safe_float(v.get("weight", 1.0), 1.0)
        if base_w <= 0:
            base_w = 1.0
        role_w = float(role_weights.get(role, 1.0))
        if role_w <= 0:
            role_w = 1.0
        w = conf * base_w * role_w
        if decision == "approve":
            approve_weight += w
            approve_conf_sum += conf
            approve_count += 1
            if role in required_set:
                voted_required.add(role)
        elif decision == "reject":
            reject_weight += w
            if role in required_set:
                voted_required.add(role)
            if role in veto_set or author.strip().lower() in veto_set:
                veto_hit = True
                veto_by.append(author)
        else:
            abstain_weight += w
            abstain_count += 1
            if role in required_set:
                voted_required.add(role)

    approve_ratio = approve_weight / participant_weight_total
    reject_ratio = reject_weight / participant_weight_total
    approve_conf_avg = (approve_conf_sum / approve_count) if approve_count > 0 else 0.0
    required_roles_missing = sorted(required_set - voted_required)
    required_roles_met = len(required_roles_missing) == 0
    abstain_policy_ok = allow_abstain or abstain_count == 0

    if veto_hit:
        decision_out = "rejected"
        reason = "veto"
    elif not abstain_policy_ok:
        decision_out = "pending"
        reason = "abstain-not-allowed"
    elif not required_roles_met:
        decision_out = "pending"
        reason = "required-roles-missing"
    elif quorum_met and critiques_ok and approve_ratio >= threshold and approve_conf_avg >= min_approve_confidence:
        decision_out = "accepted"
        reason = "threshold-met"
    elif quorum_met and reject_ratio >= reject_threshold:
        decision_out = "rejected"
        reason = "reject-threshold-met"
    else:
        decision_out = "pending"
        reason = "insufficient-consensus"

    return {
        "proposal_id": str(proposal.get("proposal_id", "")),
        "decision": decision_out,
        "reason": reason,
        "quorum_met": quorum_met,
        "quorum_weight": round(quorum_weight, 4),
        "participant_weight_total": round(participant_weight_total, 4),
        "critiques_ok": critiques_ok,
        "required_roles_met": required_roles_met,
        "required_roles_missing": required_roles_missing,
        "abstain_policy_ok": abstain_policy_ok,
        "approve_ratio": round(approve_ratio, 4),
        "reject_ratio": round(reject_ratio, 4),
        "approve_confidence_avg": round(approve_conf_avg, 4),
        "approve_weight": round(approve_weight, 4),
        "reject_weight": round(reject_weight, 4),
        "abstain_weight": round(abstain_weight, 4),
        "abstain_count": abstain_count,
        "veto_hit": veto_hit,
        "veto_by": veto_by,
        "votes_count": len(votes),
        "voters": unique_voters,
        "critiques_count": len(critiques),
    }


def finalize_round(
    project_root: Path,
    run_id: str,
    round_id: str,
    threshold: float | None = None,
    quorum_ratio: float | None = None,
    reject_threshold: float | None = None,
    min_critiques: int | None = None,
    veto_roles: List[str] | None = None,
    required_roles: List[str] | None = None,
    allow_abstain: bool | None = None,
    role_weights: Dict[str, float] | None = None,
    min_approve_confidence: float | None = None,
    single_winner: bool | None = None,
) -> Dict[str, object]:
    rnd = load_round(project_root, run_id, round_id)
    policy = _normalize_policy(rnd.policy)
    overrides: Dict[str, Any] = {}
    if threshold is not None:
        overrides["threshold"] = threshold
    if quorum_ratio is not None:
        overrides["quorum_ratio"] = quorum_ratio
    if reject_threshold is not None:
        overrides["reject_threshold"] = reject_threshold
    if min_critiques is not None:
        overrides["min_critiques"] = min_critiques
    if veto_roles is not None:
        overrides["veto_roles"] = list(veto_roles)
    if required_roles is not None:
        overrides["required_roles"] = list(required_roles)
    if allow_abstain is not None:
        overrides["allow_abstain"] = bool(allow_abstain)
    if role_weights is not None:
        overrides["role_weights"] = dict(role_weights)
    if min_approve_confidence is not None:
        overrides["min_approve_confidence"] = min_approve_confidence
    if single_winner is not None:
        overrides["single_winner"] = bool(single_winner)
    if overrides:
        merged = dict(policy)
        merged.update(overrides)
        policy = _normalize_policy(merged)
    rnd.policy = policy

    accepted: List[str] = []
    rejected: List[str] = []
    diagnostics: List[Dict[str, object]] = []

    for p in rnd.proposals:
        diag = _proposal_stats(
            proposal=p,
            participants=rnd.participants,
            threshold=_safe_float(policy.get("threshold", 0.67), 0.67),
            reject_threshold=_safe_float(policy.get("reject_threshold", policy.get("threshold", 0.67)), 0.67),
            quorum_ratio=_safe_float(policy.get("quorum_ratio", 0.6), 0.6),
            veto_roles=[str(x) for x in policy.get("veto_roles", [])],
            required_roles=[str(x) for x in policy.get("required_roles", [])],
            min_critiques=_safe_int(policy.get("min_critiques", 0), 0),
            allow_abstain=bool(policy.get("allow_abstain", True)),
            role_weights={str(k).lower(): _safe_float(v, 1.0) for k, v in dict(policy.get("role_weights", {})).items()},
            min_approve_confidence=_safe_float(policy.get("min_approve_confidence", 0.0), 0.0),
        )
        p["decision"] = str(diag["decision"])
        diagnostics.append(diag)
        if p["decision"] == "accepted":
            accepted.append(str(p.get("proposal_id")))
        elif p["decision"] == "rejected":
            rejected.append(str(p.get("proposal_id")))

    winner = ""
    if bool(policy.get("single_winner", False)) and len(accepted) > 1:
        ranking = sorted(
            [d for d in diagnostics if str(d.get("proposal_id", "")) in set(accepted)],
            key=lambda d: (
                _safe_float(d.get("approve_ratio", 0.0), 0.0),
                _safe_float(d.get("approve_weight", 0.0), 0.0),
                _safe_float(d.get("approve_confidence_avg", 0.0), 0.0),
                -_safe_float(d.get("reject_ratio", 0.0), 0.0),
            ),
            reverse=True,
        )
        if ranking:
            winner = str(ranking[0].get("proposal_id", ""))
            accepted = [winner]
            for p in rnd.proposals:
                pid = str(p.get("proposal_id", ""))
                if pid and pid != winner and str(p.get("decision", "")) == "accepted":
                    p["decision"] = "rejected"
                    if pid not in rejected:
                        rejected.append(pid)
            for d in diagnostics:
                pid = str(d.get("proposal_id", ""))
                if pid and pid != winner and str(d.get("decision", "")) == "accepted":
                    d["decision"] = "rejected"
                    d["reason"] = "single-winner-superseded"
    elif bool(policy.get("single_winner", False)) and len(accepted) == 1:
        winner = accepted[0]

    rnd.status = "closed"
    _save_round(project_root, rnd)
    return {
        "round_id": round_id,
        "accepted": accepted,
        "rejected": rejected,
        "pending": [str(p.get("proposal_id")) for p in rnd.proposals if str(p.get("decision")) == "pending"],
        "policy": policy,
        "winner": winner,
        "diagnostics": diagnostics,
    }


def evaluate_round(
    project_root: Path,
    run_id: str,
    round_id: str,
    proposal_id: str = "",
    required_decision: str = "accepted",
    finalize: bool = True,
    threshold: float | None = None,
    quorum_ratio: float | None = None,
    reject_threshold: float | None = None,
    min_critiques: int | None = None,
    veto_roles: List[str] | None = None,
    required_roles: List[str] | None = None,
    allow_abstain: bool | None = None,
    role_weights: Dict[str, float] | None = None,
    min_approve_confidence: float | None = None,
    single_winner: bool | None = None,
) -> Dict[str, object]:
    required = required_decision.strip().lower() or "accepted"
    if finalize:
        finalize_round(
            project_root=project_root,
            run_id=run_id,
            round_id=round_id,
            threshold=threshold,
            quorum_ratio=quorum_ratio,
            reject_threshold=reject_threshold,
            min_critiques=min_critiques,
            veto_roles=veto_roles,
            required_roles=required_roles,
            allow_abstain=allow_abstain,
            role_weights=role_weights,
            min_approve_confidence=min_approve_confidence,
            single_winner=single_winner,
        )
    rnd = load_round(project_root, run_id, round_id)

    target_ids: List[str]
    if proposal_id.strip():
        target_ids = [proposal_id.strip()]
    else:
        target_ids = [str(p.get("proposal_id", "")).strip() for p in rnd.proposals if str(p.get("proposal_id", "")).strip()]

    decisions: Dict[str, str] = {}
    for p in rnd.proposals:
        pid = str(p.get("proposal_id", "")).strip()
        if not pid:
            continue
        decisions[pid] = str(p.get("decision", "pending")).strip().lower()

    if not target_ids:
        return {
            "pass": False,
            "reason": "no-proposals",
            "required_decision": required,
            "decisions": decisions,
        }

    matched = all(decisions.get(pid, "pending") == required for pid in target_ids)
    return {
        "pass": matched,
        "reason": "ok" if matched else "decision-mismatch",
        "required_decision": required,
        "target_ids": target_ids,
        "decisions": decisions,
    }


def list_rounds(project_root: Path, run_id: str) -> List[str]:
    root = consensus_root(project_root) / run_id
    if not root.exists():
        return []
    return sorted([p.stem for p in root.glob("*.json")])
