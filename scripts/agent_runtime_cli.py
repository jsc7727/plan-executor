#!/usr/bin/env python3
"""Agent runtime CLI for specialist registry, message bus, and consensus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime.consensus_engine import (
    create_round,
    evaluate_round,
    finalize_round,
    list_rounds,
    load_round,
    submit_critique,
    submit_proposal,
    vote,
)
from runtime.message_bus import list_messages, send_message
from runtime.specialist_registry import get_specialist, load_registry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="plan-executor agent runtime CLI")
    parser.add_argument("--project-root", default=".", help="Project root containing .plan-executor")
    sub = parser.add_subparsers(dest="domain", required=True)

    agents = sub.add_parser("agents", help="Specialist registry commands")
    agents_sub = agents.add_subparsers(dest="cmd", required=True)
    agents_sub.add_parser("list", help="List specialists")
    ag_get = agents_sub.add_parser("get", help="Get specialist by id")
    ag_get.add_argument("--id", required=True)

    msg = sub.add_parser("message", help="Message bus commands")
    msg_sub = msg.add_subparsers(dest="cmd", required=True)
    msg_send = msg_sub.add_parser("send", help="Send message")
    msg_send.add_argument("--run-id", required=True)
    msg_send.add_argument("--from-agent", required=True)
    msg_send.add_argument("--to-agent", required=True)
    msg_send.add_argument("--kind", default="note")
    msg_send.add_argument("--content", required=True)
    msg_send.add_argument("--meta", action="append", default=[], help="metadata key=value")
    msg_list = msg_sub.add_parser("list", help="List messages")
    msg_list.add_argument("--run-id", required=True)
    msg_list.add_argument("--to-agent", default="")
    msg_list.add_argument("--from-agent", default="")
    msg_list.add_argument("--kind", default="")
    msg_list.add_argument("--limit", type=int, default=50)

    cons = sub.add_parser("consensus", help="Consensus protocol commands")
    cons_sub = cons.add_subparsers(dest="cmd", required=True)
    c_create = cons_sub.add_parser("create", help="Create consensus round")
    c_create.add_argument("--run-id", required=True)
    c_create.add_argument("--topic", required=True)
    c_create.add_argument("--participants", required=True, help="comma-separated ids")
    c_create.add_argument("--threshold", type=float, default=0.67)
    c_create.add_argument("--reject-threshold", type=float, default=0.67)
    c_create.add_argument("--quorum-ratio", type=float, default=0.6)
    c_create.add_argument("--min-critiques", type=int, default=0)
    c_create.add_argument("--veto-roles", default="", help="comma-separated role ids")
    c_create.add_argument("--required-roles", default="", help="comma-separated role ids")
    c_create.add_argument("--role-weight", action="append", default=[], help="role=weight (repeatable)")
    c_create.add_argument("--allow-abstain", action=argparse.BooleanOptionalAction, default=True)
    c_create.add_argument("--min-approve-confidence", type=float, default=0.0)
    c_create.add_argument("--single-winner", action="store_true")

    c_list = cons_sub.add_parser("list", help="List rounds for run")
    c_list.add_argument("--run-id", required=True)

    c_show = cons_sub.add_parser("show", help="Show round detail")
    c_show.add_argument("--run-id", required=True)
    c_show.add_argument("--round-id", required=True)

    c_prop = cons_sub.add_parser("propose", help="Submit proposal")
    c_prop.add_argument("--run-id", required=True)
    c_prop.add_argument("--round-id", required=True)
    c_prop.add_argument("--author", required=True)
    c_prop.add_argument("--content", required=True)

    c_crit = cons_sub.add_parser("critique", help="Submit critique")
    c_crit.add_argument("--run-id", required=True)
    c_crit.add_argument("--round-id", required=True)
    c_crit.add_argument("--proposal-id", required=True)
    c_crit.add_argument("--author", required=True)
    c_crit.add_argument("--content", required=True)

    c_vote = cons_sub.add_parser("vote", help="Vote proposal")
    c_vote.add_argument("--run-id", required=True)
    c_vote.add_argument("--round-id", required=True)
    c_vote.add_argument("--proposal-id", required=True)
    c_vote.add_argument("--author", required=True)
    c_vote.add_argument("--decision", choices=["approve", "reject", "abstain"], required=True)
    c_vote.add_argument("--confidence", type=float, default=1.0)
    c_vote.add_argument("--weight", type=float, default=1.0)
    c_vote.add_argument("--role", default="", help="Optional explicit role for weighted policy")

    c_final = cons_sub.add_parser("finalize", help="Finalize round")
    c_final.add_argument("--run-id", required=True)
    c_final.add_argument("--round-id", required=True)
    c_final.add_argument("--threshold", type=float, default=0.67)
    c_final.add_argument("--reject-threshold", type=float, default=0.67)
    c_final.add_argument("--quorum-ratio", type=float, default=0.6)
    c_final.add_argument("--min-critiques", type=int, default=0)
    c_final.add_argument("--veto-roles", default="", help="comma-separated role ids")
    c_final.add_argument("--required-roles", default="", help="comma-separated role ids")
    c_final.add_argument("--role-weight", action="append", default=[], help="role=weight (repeatable)")
    c_final.add_argument("--allow-abstain", action=argparse.BooleanOptionalAction, default=True)
    c_final.add_argument("--min-approve-confidence", type=float, default=0.0)
    c_final.add_argument("--single-winner", action="store_true")

    c_eval = cons_sub.add_parser("evaluate", help="Evaluate round/proposals against required decision")
    c_eval.add_argument("--run-id", required=True)
    c_eval.add_argument("--round-id", required=True)
    c_eval.add_argument("--proposal-id", default="")
    c_eval.add_argument("--required-decision", choices=["accepted", "rejected", "pending"], default="accepted")
    c_eval.add_argument("--finalize", action=argparse.BooleanOptionalAction, default=True)
    c_eval.add_argument("--threshold", type=float, default=0.67)
    c_eval.add_argument("--reject-threshold", type=float, default=0.67)
    c_eval.add_argument("--quorum-ratio", type=float, default=0.6)
    c_eval.add_argument("--min-critiques", type=int, default=0)
    c_eval.add_argument("--veto-roles", default="", help="comma-separated role ids")
    c_eval.add_argument("--required-roles", default="", help="comma-separated role ids")
    c_eval.add_argument("--role-weight", action="append", default=[], help="role=weight (repeatable)")
    c_eval.add_argument("--allow-abstain", action=argparse.BooleanOptionalAction, default=True)
    c_eval.add_argument("--min-approve-confidence", type=float, default=0.0)
    c_eval.add_argument("--single-winner", action="store_true")
    return parser.parse_args()


def parse_meta(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def parse_csv(text: str) -> list[str]:
    return [x.strip() for x in text.split(",") if x.strip()]


def parse_role_weights(items: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in items:
        if "=" not in item:
            continue
        role, raw_val = item.split("=", 1)
        role = role.strip().lower()
        if not role:
            continue
        try:
            val = float(raw_val.strip())
        except Exception:
            continue
        out[role] = val
    return out


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    try:
        if args.domain == "agents":
            if args.cmd == "list":
                rows = load_registry(project_root)
                print(json.dumps([r.__dict__ for r in rows], indent=2))
                return 0
            if args.cmd == "get":
                row = get_specialist(project_root, args.id)
                if not row:
                    print(f"[ERROR] specialist not found: {args.id}")
                    return 1
                print(json.dumps(row.__dict__, indent=2))
                return 0

        if args.domain == "message":
            if args.cmd == "send":
                msg = send_message(
                    project_root=project_root,
                    run_id=args.run_id,
                    from_agent=args.from_agent,
                    to_agent=args.to_agent,
                    kind=args.kind,
                    content=args.content,
                    metadata=parse_meta(args.meta),
                )
                print(json.dumps(msg.__dict__, indent=2))
                return 0
            if args.cmd == "list":
                rows = list_messages(
                    project_root=project_root,
                    run_id=args.run_id,
                    to_agent=args.to_agent,
                    from_agent=args.from_agent,
                    kind=args.kind,
                    limit=max(1, args.limit),
                )
                print(json.dumps([r.__dict__ for r in rows], indent=2))
                return 0

        if args.domain == "consensus":
            if args.cmd == "create":
                rnd = create_round(
                    project_root=project_root,
                    run_id=args.run_id,
                    topic=args.topic,
                    participants=[x.strip() for x in args.participants.split(",") if x.strip()],
                    threshold=args.threshold,
                    reject_threshold=args.reject_threshold,
                    quorum_ratio=args.quorum_ratio,
                    min_critiques=max(0, args.min_critiques),
                    veto_roles=parse_csv(args.veto_roles),
                    required_roles=parse_csv(args.required_roles),
                    role_weights=parse_role_weights(args.role_weight),
                    allow_abstain=bool(args.allow_abstain),
                    min_approve_confidence=args.min_approve_confidence,
                    single_winner=bool(args.single_winner),
                )
                print(json.dumps(rnd.__dict__, indent=2))
                return 0
            if args.cmd == "list":
                print(json.dumps({"rounds": list_rounds(project_root, args.run_id)}, indent=2))
                return 0
            if args.cmd == "show":
                rnd = load_round(project_root, args.run_id, args.round_id)
                print(json.dumps(rnd.__dict__, indent=2))
                return 0
            if args.cmd == "propose":
                row = submit_proposal(project_root, args.run_id, args.round_id, args.author, args.content)
                print(json.dumps(row, indent=2))
                return 0
            if args.cmd == "critique":
                row = submit_critique(
                    project_root,
                    args.run_id,
                    args.round_id,
                    args.proposal_id,
                    args.author,
                    args.content,
                )
                print(json.dumps(row, indent=2))
                return 0
            if args.cmd == "vote":
                row = vote(
                    project_root,
                    args.run_id,
                    args.round_id,
                    args.proposal_id,
                    args.author,
                    args.decision,
                    confidence=args.confidence,
                    weight=args.weight,
                    role=args.role,
                )
                print(json.dumps(row, indent=2))
                return 0
            if args.cmd == "finalize":
                row = finalize_round(
                    project_root,
                    args.run_id,
                    args.round_id,
                    threshold=args.threshold,
                    reject_threshold=args.reject_threshold,
                    quorum_ratio=args.quorum_ratio,
                    min_critiques=max(0, args.min_critiques),
                    veto_roles=parse_csv(args.veto_roles),
                    required_roles=parse_csv(args.required_roles),
                    role_weights=parse_role_weights(args.role_weight),
                    allow_abstain=bool(args.allow_abstain),
                    min_approve_confidence=args.min_approve_confidence,
                    single_winner=bool(args.single_winner),
                )
                print(json.dumps(row, indent=2))
                return 0
            if args.cmd == "evaluate":
                row = evaluate_round(
                    project_root=project_root,
                    run_id=args.run_id,
                    round_id=args.round_id,
                    proposal_id=args.proposal_id,
                    required_decision=args.required_decision,
                    finalize=bool(args.finalize),
                    threshold=args.threshold,
                    reject_threshold=args.reject_threshold,
                    quorum_ratio=args.quorum_ratio,
                    min_critiques=max(0, args.min_critiques),
                    veto_roles=parse_csv(args.veto_roles),
                    required_roles=parse_csv(args.required_roles),
                    role_weights=parse_role_weights(args.role_weight),
                    allow_abstain=bool(args.allow_abstain),
                    min_approve_confidence=args.min_approve_confidence,
                    single_winner=bool(args.single_winner),
                )
                print(json.dumps(row, indent=2))
                return 0

        print("[ERROR] unsupported command")
        return 1
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
