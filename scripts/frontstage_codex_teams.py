#!/usr/bin/env python3
"""Frontstage multi-agent planner using parallel Codex (or custom) CLI calls.

This script generates a frontstage plan JSON artifact that can be consumed by
hybrid_pipeline.py via --frontstage-plan.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def safe_slug(text: str) -> str:
    out = []
    for ch in text.strip().lower():
        if ch.isalnum() or ch in {"-", "_"}:
            out.append(ch)
        else:
            out.append("-")
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    slug = slug.strip("-")
    return slug or "item"


def parse_roles(value: str) -> List[str]:
    roles = [x.strip().lower() for x in value.split(",") if x.strip()]
    out: List[str] = []
    for role in roles:
        if role not in out:
            out.append(role)
    return out


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def compact_text(value: str, limit: int = 5000) -> str:
    text = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    return text[-limit:] if len(text) > limit else text


def parse_json_from_text(text: str) -> Dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except Exception:
        pass

    decoder = json.JSONDecoder()
    for i, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(raw[i:])
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return None


def to_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        token = str(item).strip()
        if token:
            out.append(token)
    return out


def extract_commands(value: Any) -> List[str]:
    if isinstance(value, str):
        token = value.strip()
        return [token] if token else []
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        if isinstance(item, str):
            token = item.strip()
            if token:
                out.append(token)
            continue
        if not isinstance(item, dict):
            continue
        for key in ("command", "cmd", "shell"):
            token = str(item.get(key, "")).strip()
            if token:
                out.append(token)
                break
    return out


def role_weights(roles: List[str]) -> Dict[str, float]:
    base = {
        "planner": 1.2,
        "architect": 1.2,
        "frontend": 1.0,
        "backend": 1.0,
        "qa": 1.1,
        "designer": 1.0,
        "security": 1.1,
        "devops": 1.0,
    }
    out: Dict[str, float] = {}
    for role in roles:
        out[role] = float(base.get(role, 1.0))
    return out


def sanitize_prompt(text: str) -> str:
    return compact_text(text).replace('"', '\\"')


def build_round_prompt(
    objective: str,
    role: str,
    round_index: int,
    candidate_snapshot: List[Dict[str, Any]],
) -> str:
    snapshot = []
    for row in candidate_snapshot[:12]:
        snapshot.append(
            {
                "proposal_id": row.get("proposal_id", ""),
                "name": row.get("name", ""),
                "owner_role": row.get("owner_role", ""),
                "summary": row.get("summary", ""),
            }
        )

    guidance = (
        "Return strict JSON only. Schema: "
        '{"proposals":[{"proposal_id":"string","name":"string","owner_role":"string","summary":"string",'
        '"commands":["string"],"risks":["string"]}],'
        '"votes":[{"proposal_id":"string","decision":"approve|reject|abstain","confidence":0.0}],'
        '"notes":["string"]}. '
        "If no vote, return empty votes list."
    )
    payload = {
        "objective": objective,
        "your_role": role,
        "phase": "propose",
        "round_index": round_index,
        "existing_candidates": snapshot,
        "instructions": guidance,
    }
    return json.dumps(payload, ensure_ascii=True)


def build_critique_prompt(
    objective: str,
    role: str,
    round_index: int,
    candidate_snapshot: List[Dict[str, Any]],
    top_k: int,
) -> str:
    snapshot = []
    for row in candidate_snapshot[: max(1, top_k)]:
        snapshot.append(
            {
                "proposal_id": row.get("proposal_id", ""),
                "name": row.get("name", ""),
                "owner_role": row.get("owner_role", ""),
                "summary": row.get("summary", ""),
                "risks": row.get("risks", []),
            }
        )
    guidance = (
        "Return strict JSON only. Schema: "
        '{"critiques":[{"proposal_id":"string","severity":"low|medium|high|critical","content":"string"}],'
        '"votes":[{"proposal_id":"string","decision":"approve|reject|abstain","confidence":0.0}],'
        '"notes":["string"]}. '
        "Focus on risk, feasibility, and testability."
    )
    payload = {
        "objective": objective,
        "your_role": role,
        "phase": "critique",
        "round_index": round_index,
        "candidate_snapshot": snapshot,
        "instructions": guidance,
    }
    return json.dumps(payload, ensure_ascii=True)


def build_revise_prompt(
    objective: str,
    role: str,
    round_index: int,
    own_candidates: List[Dict[str, Any]],
    critiques: List[Dict[str, Any]],
) -> str:
    proposals = []
    for row in own_candidates[:12]:
        proposals.append(
            {
                "proposal_id": row.get("proposal_id", ""),
                "name": row.get("name", ""),
                "summary": row.get("summary", ""),
                "commands": row.get("commands", []),
                "risks": row.get("risks", []),
            }
        )
    critique_rows = []
    for row in critiques[:20]:
        critique_rows.append(
            {
                "proposal_id": row.get("proposal_id", ""),
                "severity": row.get("severity", "medium"),
                "content": row.get("content", ""),
                "author_role": row.get("author_role", ""),
            }
        )
    guidance = (
        "Return strict JSON only. Schema: "
        '{"proposals":[{"proposal_id":"string","name":"string","owner_role":"string","summary":"string",'
        '"commands":["string"],"risks":["string"]}],'
        '"votes":[{"proposal_id":"string","decision":"approve|reject|abstain","confidence":0.0}],'
        '"notes":["string"]}. '
        "Revise your own proposals considering critiques."
    )
    payload = {
        "objective": objective,
        "your_role": role,
        "phase": "revise",
        "round_index": round_index,
        "own_candidates": proposals,
        "incoming_critiques": critique_rows,
        "instructions": guidance,
    }
    return json.dumps(payload, ensure_ascii=True)


@dataclass
class AgentResult:
    role: str
    phase: str
    round_index: int
    returncode: int
    stdout: str
    stderr: str
    payload: Dict[str, Any] | None
    error: str


@dataclass
class RoleWorker:
    role: str
    proc: subprocess.Popen[str]


def check_codex_ready(command_template: str, cwd: Path) -> tuple[bool, str]:
    template_lc = command_template.strip().lower()
    if "codex" not in template_lc:
        return True, ""
    if not shutil.which("codex"):
        return False, "codex-cli-not-found"
    probe = subprocess.run("codex login status", shell=True, cwd=str(cwd), capture_output=True, text=True)
    if probe.returncode != 0:
        return False, "codex-not-logged-in"
    return True, ""


def run_agent(
    command_template: str,
    role: str,
    phase: str,
    objective: str,
    round_index: int,
    prompt: str,
    cwd: Path,
    timeout_sec: int,
) -> AgentResult:
    context = {
        "role": role,
        "phase": phase,
        "objective": objective,
        "round_index": str(round_index),
        "prompt": sanitize_prompt(prompt),
    }
    try:
        cmd = command_template.format(**context)
    except Exception as exc:
        return AgentResult(
            role=role,
            phase=phase,
            round_index=round_index,
            returncode=1,
            stdout="",
            stderr="",
            payload=None,
            error=f"invalid-agent-command-template: {exc}",
        )

    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=max(5, timeout_sec),
        )
        payload = parse_json_from_text(proc.stdout)
        return AgentResult(
            role=role,
            phase=phase,
            round_index=round_index,
            returncode=int(proc.returncode),
            stdout=(proc.stdout or "")[-8000:],
            stderr=(proc.stderr or "")[-4000:],
            payload=payload,
            error="",
        )
    except subprocess.TimeoutExpired:
        return AgentResult(
            role=role,
            phase=phase,
            round_index=round_index,
            returncode=124,
            stdout="",
            stderr=f"timeout>{timeout_sec}s",
            payload=None,
            error="timeout",
        )


def start_role_workers(
    roles: List[str],
    project_root: Path,
    command_template: str,
    timeout_sec: int,
    memory_lines: int,
) -> Dict[str, RoleWorker]:
    script_path = Path(__file__).resolve().parent / "frontstage_role_worker.py"
    workers: Dict[str, RoleWorker] = {}
    for role in roles:
        cmd = [
            sys.executable,
            str(script_path),
            "--role",
            role,
            "--project-root",
            str(project_root),
            "--agent-cmd-template",
            command_template,
            "--timeout-sec",
            str(timeout_sec),
            "--memory-lines",
            str(memory_lines),
        ]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            cwd=str(project_root),
        )
        workers[role] = RoleWorker(role=role, proc=proc)
    return workers


def run_agent_via_worker(
    worker: RoleWorker,
    role: str,
    phase: str,
    objective: str,
    round_index: int,
    prompt: str,
) -> AgentResult:
    if worker.proc.poll() is not None:
        return AgentResult(
            role=role,
            phase=phase,
            round_index=round_index,
            returncode=1,
            stdout="",
            stderr="worker-exited",
            payload=None,
            error="worker-exited",
        )
    if worker.proc.stdin is None or worker.proc.stdout is None:
        return AgentResult(
            role=role,
            phase=phase,
            round_index=round_index,
            returncode=1,
            stdout="",
            stderr="worker-pipes-unavailable",
            payload=None,
            error="worker-pipes-unavailable",
        )

    request_id = f"req-{uuid.uuid4().hex[:12]}"
    row = {
        "kind": "run",
        "request_id": request_id,
        "role": role,
        "phase": phase,
        "objective": objective,
        "round_index": round_index,
        "prompt": prompt,
    }
    try:
        worker.proc.stdin.write(json.dumps(row, ensure_ascii=True) + "\n")
        worker.proc.stdin.flush()
    except Exception as exc:
        return AgentResult(
            role=role,
            phase=phase,
            round_index=round_index,
            returncode=1,
            stdout="",
            stderr=f"worker-write-error:{exc}",
            payload=None,
            error=f"worker-write-error:{exc}",
        )

    try:
        raw = worker.proc.stdout.readline()
    except Exception as exc:
        return AgentResult(
            role=role,
            phase=phase,
            round_index=round_index,
            returncode=1,
            stdout="",
            stderr=f"worker-read-error:{exc}",
            payload=None,
            error=f"worker-read-error:{exc}",
        )

    if not raw:
        stderr_tail = ""
        if worker.proc.stderr is not None:
            try:
                stderr_tail = (worker.proc.stderr.read() or "")[-300:]
            except Exception:
                stderr_tail = ""
        return AgentResult(
            role=role,
            phase=phase,
            round_index=round_index,
            returncode=1,
            stdout="",
            stderr=f"worker-empty-response {stderr_tail}".strip(),
            payload=None,
            error="worker-empty-response",
        )

    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("worker response must be object")
    except Exception as exc:
        return AgentResult(
            role=role,
            phase=phase,
            round_index=round_index,
            returncode=1,
            stdout="",
            stderr=f"worker-invalid-json:{exc}",
            payload=None,
            error=f"worker-invalid-json:{exc}",
        )

    return AgentResult(
        role=role,
        phase=phase,
        round_index=round_index,
        returncode=int(parsed.get("returncode", 1)),
        stdout=str(parsed.get("stdout", "")),
        stderr=str(parsed.get("stderr", "")),
        payload=parsed.get("payload", None) if isinstance(parsed.get("payload", None), dict) else None,
        error=str(parsed.get("error", "")),
    )


def stop_role_workers(workers: Dict[str, RoleWorker]) -> List[Dict[str, Any]]:
    diagnostics: List[Dict[str, Any]] = []
    for role, worker in workers.items():
        proc = worker.proc
        note: Dict[str, Any] = {"role": role, "stopped": False, "error": ""}
        try:
            if proc.poll() is None and proc.stdin is not None and proc.stdout is not None:
                payload = {"kind": "shutdown", "request_id": f"stop-{uuid.uuid4().hex[:8]}"}
                proc.stdin.write(json.dumps(payload, ensure_ascii=True) + "\n")
                proc.stdin.flush()
                _ = proc.stdout.readline()
            proc.wait(timeout=2.0)
            note["stopped"] = True
        except Exception as exc:
            note["error"] = str(exc)
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
                note["stopped"] = True
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        diagnostics.append(note)
    return diagnostics


def normalize_proposals(payload: Dict[str, Any] | None, role: str, round_index: int) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    rows = payload.get("proposals", [])
    if not isinstance(rows, list) or not rows:
        stage_rows = payload.get("stages", [])
        if isinstance(stage_rows, list) and stage_rows:
            rows = stage_rows
        else:
            rows = [payload]

    out: List[Dict[str, Any]] = []
    for i, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        pid = str(row.get("proposal_id", "")).strip() or str(row.get("stage_id", "")).strip()
        if not pid:
            pid = f"{role}-r{round_index}-p{i}"
        name = str(row.get("name", "")).strip() or str(row.get("title", "")).strip() or f"{role}-proposal-{i}"
        owner_role = str(row.get("owner_role", "")).strip().lower() or str(row.get("role", "")).strip().lower() or role
        summary = str(row.get("summary", "")).strip() or str(row.get("content", "")).strip() or name
        commands = extract_commands(row.get("commands", []))
        if not commands:
            commands = extract_commands(row.get("tasks", []))
        risks = to_string_list(row.get("risks", []))
        out.append(
            {
                "proposal_id": pid,
                "name": name,
                "owner_role": owner_role,
                "summary": summary,
                "commands": commands,
                "risks": risks,
                "source_role": role,
                "round_index": round_index,
            }
        )
    return out


def normalize_votes(payload: Dict[str, Any] | None, role: str) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("votes", [])
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("proposal_id", "")).strip() or str(row.get("stage_id", "")).strip() or str(row.get("stage_ref", "")).strip()
        if not pid:
            continue
        decision = str(row.get("decision", "approve")).strip().lower()
        if decision not in {"approve", "reject", "abstain"}:
            continue
        try:
            conf = float(row.get("confidence", 1.0))
        except Exception:
            conf = 1.0
        if conf < 0.0:
            conf = 0.0
        if conf > 1.0:
            conf = 1.0
        out.append(
            {
                "author_role": role,
                "proposal_id": pid,
                "decision": decision,
                "confidence": conf,
            }
        )
    return out


def normalize_critiques(payload: Dict[str, Any] | None, role: str) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("critiques", [])
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = (
            str(row.get("proposal_id", "")).strip()
            or str(row.get("stage_id", "")).strip()
            or str(row.get("stage_ref", "")).strip()
        )
        if not pid:
            continue
        severity = str(row.get("severity", "medium")).strip().lower()
        if severity not in {"low", "medium", "high", "critical"}:
            severity = "medium"
        content = str(row.get("content", "")).strip() or str(row.get("summary", "")).strip()
        if not content:
            continue
        out.append(
            {
                "author_role": role,
                "proposal_id": pid,
                "severity": severity,
                "content": content,
            }
        )
    return out


def merge_proposal(
    candidates_by_id: Dict[str, Dict[str, Any]],
    candidate_order: List[str],
    proposal: Dict[str, Any],
) -> None:
    pid = str(proposal.get("proposal_id", "")).strip()
    if not pid:
        return
    if pid not in candidates_by_id:
        out = dict(proposal)
        out.setdefault("critiques", [])
        candidates_by_id[pid] = out
        candidate_order.append(pid)
        return

    prev = candidates_by_id[pid]
    for key in ("name", "owner_role", "summary", "source_role"):
        value = str(proposal.get(key, "")).strip()
        if value:
            prev[key] = value
    for key in ("round_index",):
        if key in proposal:
            prev[key] = proposal[key]
    for key in ("commands", "risks"):
        rows = proposal.get(key, [])
        if isinstance(rows, list) and rows:
            prev[key] = [str(x) for x in rows if str(x).strip()]
    prev.setdefault("critiques", [])


def score_candidates(
    candidates: List[Dict[str, Any]],
    votes: List[Dict[str, Any]],
    critiques: List[Dict[str, Any]],
    roles: List[str],
    accept_threshold: float,
    quorum_ratio: float,
) -> Dict[str, Any]:
    candidates_by_id = {str(c["proposal_id"]): c for c in candidates}
    weights = role_weights(roles)
    stats: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        pid = str(candidate["proposal_id"])
        stats[pid] = {
            "approve_score": 0.0,
            "reject_score": 0.0,
            "critique_penalty": 0.0,
            "critique_count": 0,
            "abstain_count": 0,
            "votes": [],
            "voters": set(),
            "critiques": [],
        }

    for row in votes:
        pid = str(row.get("proposal_id", "")).strip()
        if pid not in stats:
            continue
        author = str(row.get("author_role", "")).strip().lower()
        decision = str(row.get("decision", "abstain")).strip().lower()
        conf = float(row.get("confidence", 1.0))
        weight = float(weights.get(author, 1.0))
        score = conf * weight
        if decision == "approve":
            stats[pid]["approve_score"] += score
        elif decision == "reject":
            stats[pid]["reject_score"] += score
        else:
            stats[pid]["abstain_count"] += 1
        stats[pid]["voters"].add(author)
        stats[pid]["votes"].append(
            {
                "author_role": author,
                "decision": decision,
                "confidence": conf,
                "weight": round(weight, 4),
            }
        )

    sev_weight = {
        "low": 0.05,
        "medium": 0.12,
        "high": 0.2,
        "critical": 0.3,
    }
    for row in critiques:
        pid = str(row.get("proposal_id", "")).strip()
        if pid not in stats:
            continue
        author = str(row.get("author_role", "")).strip().lower()
        severity = str(row.get("severity", "medium")).strip().lower()
        content = str(row.get("content", "")).strip()
        if severity not in sev_weight:
            severity = "medium"
        weight = float(weights.get(author, 1.0))
        penalty = float(sev_weight[severity]) * weight
        stats[pid]["critique_penalty"] += penalty
        stats[pid]["critique_count"] += 1
        stats[pid]["critiques"].append(
            {
                "author_role": author,
                "severity": severity,
                "content": content,
                "weight": round(weight, 4),
                "penalty": round(penalty, 4),
            }
        )

    scored: List[Dict[str, Any]] = []
    accepted_ids: List[str] = []
    rejected_ids: List[str] = []
    total_roles = max(1, len(roles))
    for candidate in candidates:
        pid = str(candidate["proposal_id"])
        row = stats[pid]
        approve_score = float(row["approve_score"])
        reject_score = float(row["reject_score"])
        critique_penalty = float(row["critique_penalty"])
        score = approve_score - reject_score - critique_penalty
        voters = sorted([str(x) for x in row["voters"] if str(x).strip()])
        q = len(voters) / total_roles
        accepted = score >= accept_threshold and q >= quorum_ratio
        status = "accepted" if accepted else "rejected"
        scored_row = {
            **candidate,
            "status": status,
            "score": round(score, 4),
            "approve_score": round(approve_score, 4),
            "reject_score": round(reject_score, 4),
            "critique_penalty": round(critique_penalty, 4),
            "critique_count": int(row["critique_count"]),
            "quorum_ratio": round(q, 4),
            "voters": voters,
            "votes": row["votes"],
            "critiques": row["critiques"],
            "abstain_count": int(row["abstain_count"]),
        }
        scored.append(scored_row)
        if accepted:
            accepted_ids.append(pid)
        else:
            rejected_ids.append(pid)

    scored.sort(key=lambda x: (float(x.get("score", 0.0)), float(x.get("approve_score", 0.0))), reverse=True)
    if not accepted_ids and scored:
        # Ensure at least one stage survives even in strict settings.
        scored[0]["status"] = "accepted"
        accepted_ids = [str(scored[0]["proposal_id"])]
        rejected_ids = [str(x["proposal_id"]) for x in scored[1:]]
    return {
        "candidates_scored": scored,
        "accepted_ids": accepted_ids,
        "rejected_ids": rejected_ids,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Frontstage parallel Codex teams planner")
    parser.add_argument("--project-root", default=".", help="Project root containing .plan-executor")
    parser.add_argument("--objective", required=True, help="Planning objective")
    parser.add_argument(
        "--roles",
        default="planner,architect,frontend,backend,qa",
        help="Comma-separated frontstage roles",
    )
    parser.add_argument("--rounds", type=int, default=2, help="Consensus rounds")
    parser.add_argument(
        "--debate-mode",
        choices=["none", "critique-revise"],
        default="critique-revise",
        help="Debate loop strategy per round",
    )
    parser.add_argument("--critique-top-k", type=int, default=8, help="Top candidates shared during critique phase")
    parser.add_argument("--max-parallel-agents", type=int, default=0, help="Parallel workers (0=roles count)")
    parser.add_argument(
        "--agent-runtime",
        choices=["auto", "oneshot", "persistent"],
        default="auto",
        help="Agent execution runtime model",
    )
    parser.add_argument("--worker-memory-lines", type=int, default=30, help="Role worker memory capacity")
    parser.add_argument("--timeout-sec", type=int, default=180, help="Per-agent timeout seconds")
    parser.add_argument(
        "--agent-cmd-template",
        default='codex exec --skip-git-repo-check "{prompt}"',
        help="Agent command template. Placeholders: {prompt}, {role}, {phase}, {objective}, {round_index}",
    )
    parser.add_argument("--skip-codex-check", action="store_true", help="Skip codex availability/login probe")
    parser.add_argument("--accept-threshold", type=float, default=0.3, help="Consensus accept score threshold")
    parser.add_argument("--quorum-ratio", type=float, default=0.34, help="Consensus quorum ratio [0-1]")
    parser.add_argument("--max-stages", type=int, default=8, help="Max accepted stages exported")
    parser.add_argument("--output", default="", help="Output frontstage plan JSON path")
    parser.add_argument("--json-stdout", action="store_true", help="Print generated plan JSON to stdout")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    roles = parse_roles(args.roles)
    if not roles:
        print("[ERROR] roles must not be empty")
        return 2

    rounds = max(1, int(args.rounds))
    max_parallel = int(args.max_parallel_agents)
    if max_parallel <= 0:
        max_parallel = len(roles)
    max_parallel = max(1, max_parallel)
    timeout_sec = max(10, int(args.timeout_sec))
    accept_threshold = float(args.accept_threshold)
    quorum_ratio = float(args.quorum_ratio)
    if quorum_ratio < 0.0:
        quorum_ratio = 0.0
    if quorum_ratio > 1.0:
        quorum_ratio = 1.0
    max_stages = max(1, int(args.max_stages))
    debate_mode = str(args.debate_mode).strip().lower()
    critique_top_k = max(1, int(args.critique_top_k))
    runtime_mode = str(args.agent_runtime).strip().lower()
    if runtime_mode == "auto":
        runtime_mode = "persistent"
    worker_memory_lines = max(0, int(args.worker_memory_lines))

    if not args.skip_codex_check:
        ok, reason = check_codex_ready(args.agent_cmd_template, project_root)
        if not ok:
            print(f"[ERROR] {reason}")
            return 2

    out_path = Path(args.output).resolve() if args.output else (
        project_root / ".plan-executor" / "frontstage" / f"plan-{utc_compact()}.json"
    )

    candidates_by_id: Dict[str, Dict[str, Any]] = {}
    candidate_order: List[str] = []
    votes: List[Dict[str, Any]] = []
    critiques: List[Dict[str, Any]] = []
    call_trace: List[Dict[str, Any]] = []
    phase_counts: Dict[str, int] = {"propose": 0, "critique": 0, "revise": 0}
    role_workers: Dict[str, RoleWorker] = {}
    worker_shutdown_diag: List[Dict[str, Any]] = []

    if runtime_mode == "persistent":
        role_workers = start_role_workers(
            roles=roles,
            project_root=project_root,
            command_template=args.agent_cmd_template,
            timeout_sec=timeout_sec,
            memory_lines=worker_memory_lines,
        )
    try:
        for round_index in range(1, rounds + 1):
            phases: List[str] = ["propose"]
            if debate_mode == "critique-revise":
                phases.extend(["critique", "revise"])

            for phase in phases:
                snapshot = [candidates_by_id[x] for x in candidate_order if x in candidates_by_id]
                if phase == "revise":
                    phase_roles = sorted(
                        {
                            str(c.get("owner_role", "")).strip().lower()
                            for c in snapshot
                            if str(c.get("owner_role", "")).strip()
                        }
                    )
                    if not phase_roles:
                        phase_roles = roles
                else:
                    phase_roles = roles

                futures = {}
                with ThreadPoolExecutor(max_workers=max_parallel) as executor:
                    for role in phase_roles:
                        if phase == "propose":
                            prompt = build_round_prompt(args.objective, role, round_index, snapshot)
                        elif phase == "critique":
                            prompt = build_critique_prompt(args.objective, role, round_index, snapshot, critique_top_k)
                        else:
                            role_candidates = [x for x in snapshot if str(x.get("owner_role", "")).strip().lower() == role]
                            role_candidate_ids = {str(x.get("proposal_id", "")).strip() for x in role_candidates}
                            role_critiques = [x for x in critiques if str(x.get("proposal_id", "")).strip() in role_candidate_ids]
                            prompt = build_revise_prompt(args.objective, role, round_index, role_candidates, role_critiques)

                        if runtime_mode == "persistent":
                            worker = role_workers.get(role)
                            if not worker:
                                future = executor.submit(
                                    run_agent,
                                    args.agent_cmd_template,
                                    role,
                                    phase,
                                    args.objective,
                                    round_index,
                                    prompt,
                                    project_root,
                                    timeout_sec,
                                )
                            else:
                                future = executor.submit(
                                    run_agent_via_worker,
                                    worker,
                                    role,
                                    phase,
                                    args.objective,
                                    round_index,
                                    prompt,
                                )
                        else:
                            future = executor.submit(
                                run_agent,
                                args.agent_cmd_template,
                                role,
                                phase,
                                args.objective,
                                round_index,
                                prompt,
                                project_root,
                                timeout_sec,
                            )
                        futures[future] = role

                    for future in as_completed(futures):
                        result = future.result()
                        phase_counts[result.phase] = int(phase_counts.get(result.phase, 0)) + 1
                        call_trace.append(
                            {
                                "role": result.role,
                                "phase": result.phase,
                                "round_index": result.round_index,
                                "returncode": result.returncode,
                                "payload_ok": isinstance(result.payload, dict),
                                "stderr_tail": (result.stderr or "")[-200:],
                                "error": result.error,
                            }
                        )

                        proposals = normalize_proposals(result.payload, role=result.role, round_index=round_index)
                        if result.phase != "critique":
                            for proposal in proposals:
                                merge_proposal(candidates_by_id, candidate_order, proposal)

                        normalized_votes = normalize_votes(result.payload, role=result.role)
                        if normalized_votes:
                            votes.extend(normalized_votes)

                        normalized_critiques = normalize_critiques(result.payload, role=result.role)
                        for critique in normalized_critiques:
                            pid = str(critique.get("proposal_id", "")).strip()
                            if pid not in candidates_by_id:
                                continue
                            critiques.append(critique)
                            candidate_crits = candidates_by_id[pid].setdefault("critiques", [])
                            if isinstance(candidate_crits, list):
                                candidate_crits.append(critique)

                        if result.phase in {"propose", "revise"}:
                            # Self-vote default keeps progress when explicit votes are omitted.
                            for proposal in proposals:
                                pid = str(proposal.get("proposal_id", "")).strip()
                                if not pid:
                                    continue
                                votes.append(
                                    {
                                        "author_role": result.role,
                                        "proposal_id": pid,
                                        "decision": "approve",
                                        "confidence": 0.8 if result.phase == "propose" else 0.7,
                                    }
                                )
    finally:
        if role_workers:
            worker_shutdown_diag = stop_role_workers(role_workers)

    candidates = [candidates_by_id[x] for x in candidate_order if x in candidates_by_id]
    if not candidates:
        print("[ERROR] no proposals generated by frontstage agents")
        return 1

    scored = score_candidates(
        candidates=candidates,
        votes=votes,
        critiques=critiques,
        roles=roles,
        accept_threshold=accept_threshold,
        quorum_ratio=quorum_ratio,
    )
    candidates_scored = list(scored["candidates_scored"])
    accepted = [x for x in candidates_scored if str(x.get("status", "")) == "accepted"][:max_stages]

    stages: List[Dict[str, Any]] = []
    for row in accepted:
        stages.append(
            {
                "name": str(row.get("name", "")),
                "owner_role": str(row.get("owner_role", "")),
                "summary": str(row.get("summary", "")),
                "commands": [str(x) for x in row.get("commands", []) if str(x).strip()],
                "risks": [str(x) for x in row.get("risks", []) if str(x).strip()],
                "consensus_score": float(row.get("score", 0.0)),
                "consensus_quorum_ratio": float(row.get("quorum_ratio", 0.0)),
                "proposal_id": str(row.get("proposal_id", "")),
            }
        )

    plan_payload = {
        "meta": {
            "generated_at_utc": utc_compact(),
            "planner": "frontstage-codex-teams",
            "run_id": f"frontstage-{utc_compact()}-{uuid.uuid4().hex[:6]}",
            "roles": roles,
            "rounds": rounds,
            "debate_mode": debate_mode,
            "agent_runtime": runtime_mode,
            "engine_hint": "codex" if "codex" in args.agent_cmd_template.lower() else "custom-cli",
        },
        "objective": args.objective,
        "stages": stages,
        "consensus": {
            "policy": {
                "accept_threshold": accept_threshold,
                "quorum_ratio": quorum_ratio,
                "max_stages": max_stages,
                "critique_top_k": critique_top_k,
            },
            "candidate_count": len(candidates_scored),
            "critique_count": len(critiques),
            "accepted_ids": [str(x.get("proposal_id", "")) for x in accepted],
            "rejected_ids": [str(x.get("proposal_id", "")) for x in candidates_scored if str(x.get("status", "")) != "accepted"],
            "candidates_scored": candidates_scored,
        },
        "trace": {
            "agent_calls": call_trace,
            "phase_counts": phase_counts,
            "vote_count": len(votes),
            "critique_count": len(critiques),
            "worker_shutdown": worker_shutdown_diag,
        },
    }

    write_json(out_path, plan_payload)
    print(f"[OK] frontstage plan written: {out_path}")
    print(
        f"[OK] stages={len(stages)} candidates={len(candidates_scored)} votes={len(votes)} "
        f"critiques={len(critiques)} mode={debate_mode} runtime={runtime_mode}"
    )
    if args.json_stdout:
        print(json.dumps(plan_payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
