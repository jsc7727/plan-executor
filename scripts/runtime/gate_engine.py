#!/usr/bin/env python3
"""Checkpoint gate evaluation for plan-executor runtime."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from .command_guardrails import resolve_command_guardrail, resolve_guardrail_policy_for_context
from .consensus_engine import create_round, evaluate_round, submit_proposal, vote
from .consensus_templates import load_synthetic_votes_template


@dataclass
class GateResult:
    status: str
    evidence: List[str]
    commands: List[Dict[str, Any]]
    error: str = ""


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


def _to_tokens(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        token = str(item).strip()
        if token:
            out.append(token)
    return out


def _to_participants(value: Any) -> List[str]:
    if isinstance(value, list):
        return _to_tokens(value)
    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]
    return []


def _to_role_weights(value: Any) -> Dict[str, float]:
    if not isinstance(value, dict):
        return {}
    out: Dict[str, float] = {}
    for k, v in value.items():
        role = str(k).strip().lower()
        if not role:
            continue
        out[role] = _safe_float(v, 1.0)
    return out


def evaluate_checkpoint(checkpoint: Dict[str, Any], project_root: Path, run_id: str = "", limits: Dict[str, Any] | None = None) -> GateResult:
    gate_criteria = checkpoint.get("gate_criteria", [])
    gate_commands = checkpoint.get("gate_commands", [])
    consensus_gate = checkpoint.get("consensus_gate", {})
    timeout_sec = int(checkpoint.get("gate_timeout_sec", 120))
    evidence: List[str] = []
    command_results: List[Dict[str, Any]] = []
    limits = dict(limits or {})
    cp_guardrails = checkpoint.get("command_guardrails", None)
    global_guardrails = limits.get("command_guardrails", {})
    raw_guardrail_policy = cp_guardrails if isinstance(cp_guardrails, dict) else global_guardrails
    guardrail_environment = str(limits.get("guardrail_environment", "")).strip()
    guardrail_policy = resolve_guardrail_policy_for_context(
        raw_policy=raw_guardrail_policy,
        role="integrator",
        environment=guardrail_environment,
    )

    if gate_criteria:
        evidence.append(f"criteria-count:{len(gate_criteria)}")

    has_consensus_gate = isinstance(consensus_gate, dict) and bool(consensus_gate)
    if not gate_commands and not has_consensus_gate:
        evidence.append("criteria-only-no-commands")
        return GateResult(status="pass", evidence=evidence, commands=command_results)

    for raw_cmd in gate_commands:
        cmd = str(raw_cmd).strip()
        if not cmd:
            continue
        guard = resolve_command_guardrail(
            cmd=cmd,
            policy=guardrail_policy,
            phase="gate",
            context={
                "project_root": str(project_root),
                "run_id": str(run_id).strip(),
                "checkpoint_id": str(checkpoint.get("id", "")).strip(),
                "owner_role": "integrator",
                "environment": guardrail_environment,
                "phase": "gate",
            },
        )
        if bool(guard.get("audit_only", False)):
            evidence.append(f"gate-guardrail-audit:{guard.get('reason', '')}")
        if not bool(guard.get("allowed", True)):
            command_results.append(
                {
                    "cmd": cmd,
                    "returncode": 126,
                    "stdout": "",
                    "stderr": f"blocked by command guardrail: {guard.get('reason', '')}",
                    "guardrail": guard,
                }
            )
            evidence.append("gate-command-guardrail-blocked")
            return GateResult(
                status="fail",
                evidence=evidence,
                commands=command_results,
                error=f"gate command blocked by guardrail: {cmd}",
            )
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            command_results.append(
                {
                    "cmd": cmd,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout[-4000:],
                    "stderr": proc.stderr[-4000:],
                }
            )
            if proc.returncode != 0:
                evidence.append("gate-command-failed")
                return GateResult(
                    status="fail",
                    evidence=evidence,
                    commands=command_results,
                    error=f"gate command failed: {cmd}",
                )
        except subprocess.TimeoutExpired:
            command_results.append({"cmd": cmd, "returncode": 124, "stdout": "", "stderr": "timeout"})
            evidence.append("gate-command-timeout")
            return GateResult(
                status="fail",
                evidence=evidence,
                commands=command_results,
                error=f"gate command timeout: {cmd}",
            )

    if gate_commands:
        evidence.append("gate-commands-pass")

    if has_consensus_gate:
        if not run_id.strip():
            evidence.append("consensus-gate-failed")
            command_results.append({"kind": "consensus", "error": "missing-run-id"})
            return GateResult(
                status="fail",
                evidence=evidence,
                commands=command_results,
                error="consensus gate requires run_id",
            )
        round_id = str(consensus_gate.get("round_id", "")).strip()
        proposal_id = str(consensus_gate.get("proposal_id", "")).strip()
        if not round_id:
            if not _safe_bool(consensus_gate.get("auto_create_round", False), False):
                evidence.append("consensus-gate-failed")
                command_results.append({"kind": "consensus", "error": "missing-round-id"})
                return GateResult(
                    status="fail",
                    evidence=evidence,
                    commands=command_results,
                    error="consensus gate requires round_id",
                )
            participants = _to_participants(consensus_gate.get("participants", []))
            if not participants:
                participants = _to_tokens(consensus_gate.get("required_roles", []))
            if not participants:
                participants = ["planner", "qa"]
            try:
                created_round = create_round(
                    project_root=project_root,
                    run_id=run_id.strip(),
                    topic=str(consensus_gate.get("topic", "checkpoint-consensus")).strip() or "checkpoint-consensus",
                    participants=participants,
                    threshold=_safe_float(consensus_gate.get("threshold", 0.67), 0.67),
                    reject_threshold=_safe_float(consensus_gate.get("reject_threshold", 0.67), 0.67),
                    quorum_ratio=_safe_float(consensus_gate.get("quorum_ratio", 0.6), 0.6),
                    min_critiques=_safe_int(consensus_gate.get("min_critiques", 0), 0),
                    veto_roles=_to_tokens(consensus_gate.get("veto_roles", [])),
                    required_roles=_to_tokens(consensus_gate.get("required_roles", [])),
                    allow_abstain=_safe_bool(consensus_gate.get("allow_abstain", True), True),
                    role_weights=_to_role_weights(consensus_gate.get("role_weights", {})),
                    min_approve_confidence=_safe_float(consensus_gate.get("min_approve_confidence", 0.0), 0.0),
                    single_winner=_safe_bool(consensus_gate.get("single_winner", False), False),
                )
                round_id = created_round.round_id
                evidence.append("consensus-round-auto-created")

                created_prop = submit_proposal(
                    project_root=project_root,
                    run_id=run_id.strip(),
                    round_id=round_id,
                    author=str(consensus_gate.get("proposal_author", participants[0])).strip() or participants[0],
                    content=str(consensus_gate.get("proposal_content", "auto-generated-proposal")).strip() or "auto-generated-proposal",
                )
                auto_proposal_id = str(created_prop.get("proposal_id", "")).strip()
                if not proposal_id:
                    proposal_id = auto_proposal_id
                evidence.append("consensus-proposal-auto-created")

                synthetic_votes = consensus_gate.get("synthetic_votes", [])
                template_id = ""
                template_path = ""
                template_ref = str(consensus_gate.get("synthetic_votes_template", "")).strip()
                if (not isinstance(synthetic_votes, list) or not synthetic_votes) and template_ref:
                    try:
                        template_id, template_votes, template_path = load_synthetic_votes_template(project_root, template_ref)
                        synthetic_votes = template_votes
                        evidence.append(f"consensus-synthetic-template:{template_id}")
                    except Exception as exc:
                        if _safe_bool(consensus_gate.get("strict_template", False), False):
                            raise
                        command_results.append(
                            {
                                "kind": "consensus",
                                "template_ref": template_ref,
                                "template_error": str(exc),
                            }
                        )
                        evidence.append("consensus-synthetic-template-missing")
                vote_count = 0
                if isinstance(synthetic_votes, list) and synthetic_votes:
                    for row in synthetic_votes:
                        if not isinstance(row, dict):
                            continue
                        author = str(row.get("author", "")).strip()
                        if not author:
                            continue
                        decision = str(row.get("decision", "approve")).strip().lower()
                        if decision not in {"approve", "reject", "abstain"}:
                            continue
                        conf = _safe_float(row.get("confidence", 1.0), 1.0)
                        if conf < 0.0:
                            conf = 0.0
                        if conf > 1.0:
                            conf = 1.0
                        wt = _safe_float(row.get("weight", 1.0), 1.0)
                        if wt <= 0:
                            wt = 1.0
                        vote(
                            project_root=project_root,
                            run_id=run_id.strip(),
                            round_id=round_id,
                            proposal_id=auto_proposal_id,
                            author=author,
                            role=str(row.get("role", author)).strip() or author,
                            decision=decision,
                            confidence=conf,
                            weight=wt,
                        )
                        vote_count += 1
                else:
                    vote_mode = str(consensus_gate.get("auto_vote_mode", "approve-all")).strip().lower()
                    if vote_mode in {"approve-all", "reject-all"}:
                        decision = "approve" if vote_mode == "approve-all" else "reject"
                        conf = _safe_float(consensus_gate.get("auto_vote_confidence", 1.0), 1.0)
                        if conf < 0.0:
                            conf = 0.0
                        if conf > 1.0:
                            conf = 1.0
                        for participant in participants:
                            vote(
                                project_root=project_root,
                                run_id=run_id.strip(),
                                round_id=round_id,
                                proposal_id=auto_proposal_id,
                                author=participant,
                                role=participant,
                                decision=decision,
                                confidence=conf,
                                weight=1.0,
                            )
                            vote_count += 1

                command_results.append(
                    {
                        "kind": "consensus",
                        "auto_create_round": True,
                        "round_id": round_id,
                        "proposal_id": proposal_id,
                        "participants": participants,
                        "template_id": template_id,
                        "template_path": template_path,
                        "auto_vote_count": vote_count,
                    }
                )
                if vote_count > 0:
                    evidence.append(f"consensus-auto-votes:{vote_count}")
            except Exception as exc:
                evidence.append("consensus-gate-failed")
                command_results.append({"kind": "consensus", "error": f"autocreate-error:{exc}"})
                return GateResult(
                    status="fail",
                    evidence=evidence,
                    commands=command_results,
                    error=f"consensus gate auto-create error: {exc}",
                )
        try:
            eval_out = evaluate_round(
                project_root=project_root,
                run_id=run_id.strip(),
                round_id=round_id,
                proposal_id=proposal_id,
                required_decision=str(consensus_gate.get("required_decision", "accepted")).strip() or "accepted",
                finalize=_safe_bool(consensus_gate.get("finalize", True), True),
                threshold=_safe_float(consensus_gate.get("threshold", 0.67), 0.67),
                reject_threshold=_safe_float(consensus_gate.get("reject_threshold", 0.67), 0.67),
                quorum_ratio=_safe_float(consensus_gate.get("quorum_ratio", 0.6), 0.6),
                min_critiques=_safe_int(consensus_gate.get("min_critiques", 0), 0),
                veto_roles=_to_tokens(consensus_gate.get("veto_roles", [])),
                required_roles=_to_tokens(consensus_gate.get("required_roles", [])),
                allow_abstain=_safe_bool(consensus_gate.get("allow_abstain", True), True),
                role_weights=_to_role_weights(consensus_gate.get("role_weights", {})),
                min_approve_confidence=_safe_float(consensus_gate.get("min_approve_confidence", 0.0), 0.0),
                single_winner=_safe_bool(consensus_gate.get("single_winner", False), False),
            )
        except Exception as exc:
            evidence.append("consensus-gate-failed")
            command_results.append({"kind": "consensus", "round_id": round_id, "error": str(exc)})
            return GateResult(
                status="fail",
                evidence=evidence,
                commands=command_results,
                error=f"consensus gate error: {exc}",
            )

        command_results.append(
            {
                "kind": "consensus",
                "round_id": round_id,
                "proposal_id": proposal_id,
                "required_decision": str(consensus_gate.get("required_decision", "accepted")).strip() or "accepted",
                "result": eval_out,
            }
        )
        if bool(eval_out.get("pass", False)):
            evidence.append("consensus-gate-pass")
        else:
            evidence.append("consensus-gate-failed")
            return GateResult(
                status="fail",
                evidence=evidence,
                commands=command_results,
                error=f"consensus gate failed: {eval_out.get('reason', 'decision-mismatch')}",
            )

    return GateResult(status="pass", evidence=evidence, commands=command_results)
