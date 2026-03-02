#!/usr/bin/env python3
"""Bootstrap a plan-executor runbook JSON artifact."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from runtime.command_guardrails import known_guardrail_profiles, known_os_templates


PRESETS: Dict[str, Dict[str, List[str]]] = {
    "metagpt-swe-line": {
        "paper_basis": ["MetaGPT"],
        "roles": ["product-manager", "architect", "project-manager", "engineer", "qa-engineer"],
    },
    "chatdev-waterfall": {
        "paper_basis": ["ChatDev"],
        "roles": ["ceo", "cpo", "cto", "programmer", "art-designer", "reviewer", "tester"],
    },
    "autogen-duo-plus": {
        "paper_basis": ["AutoGen"],
        "roles": ["assistant-agent", "user-proxy-agent", "group-chat-manager"],
    },
    "product-web-app": {
        "paper_basis": ["MetaGPT", "ChatDev"],
        "roles": ["planner", "designer", "frontend", "backend", "qa"],
    },
}


PROFILES = {
    "speed": {
        "checkpoint_strictness": "minimal",
        "validation_policy": "targeted-only",
        "max_lanes": 4,
        "gates": {
            "code": ["targeted-tests-pass"],
            "document": ["required-sections-complete"],
            "research": ["recommendation-present"],
        },
    },
    "balanced": {
        "checkpoint_strictness": "standard",
        "validation_policy": "targeted-plus-one-broad",
        "max_lanes": 3,
        "gates": {
            "code": ["targeted-tests-pass", "lint-clean", "one-broad-check-pass"],
            "document": ["required-sections-complete", "factual-claims-sourced"],
            "research": ["recommendation-present", "dated-sources-attached"],
        },
    },
    "hardening": {
        "checkpoint_strictness": "strict",
        "validation_policy": "targeted-plus-broad-plus-regression",
        "max_lanes": 2,
        "gates": {
            "code": ["targeted-tests-pass", "lint-clean", "broad-check-pass", "regression-bundle-pass"],
            "document": ["required-sections-complete", "factual-claims-sourced", "consistency-review-pass"],
            "research": ["recommendation-present", "dated-sources-attached", "tradeoff-table-complete"],
        },
    },
}

PROFILE_TO_GUARDRAIL = {
    "speed": "dev",
    "balanced": "ci",
    "hardening": "prod",
}

PROFILE_TO_ENVIRONMENT = {
    "speed": "dev",
    "balanced": "ci",
    "hardening": "prod",
}


def parse_role_policy_rows(rows: List[str]) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for raw in rows:
        token = str(raw).strip()
        if not token or ":" not in token:
            continue
        role, mode = token.split(":", 1)
        role_key = role.strip().lower()
        mode_key = mode.strip().lower()
        if not role_key:
            continue
        if mode_key not in {"enforce", "audit", "human-approval"}:
            continue
        out[role_key] = {"mode": mode_key}
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a runbook JSON for plan-executor.")
    parser.add_argument(
        "--preset",
        choices=sorted(PRESETS.keys()),
        default="product-web-app",
        help="Role preset for lane assignment.",
    )
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILES.keys()),
        default="balanced",
        help="Execution profile controlling strictness.",
    )
    parser.add_argument(
        "--lanes",
        type=int,
        default=3,
        help="Number of lanes (1-4). Additional caps apply by profile.",
    )
    parser.add_argument(
        "--mode",
        choices=["sequential", "parallel"],
        default="parallel",
        help="Requested run mode.",
    )
    parser.add_argument(
        "--max-parallel-workers",
        type=int,
        default=0,
        help="Override max parallel workers. Defaults to mode/profile-derived value.",
    )
    parser.add_argument(
        "--task-type",
        choices=["code", "document", "research"],
        default="code",
        help="Primary task type for gate selection.",
    )
    parser.add_argument(
        "--gate-cmd",
        action="append",
        default=[],
        help="Checkpoint gate command (repeatable).",
    )
    parser.add_argument(
        "--consensus-round-id",
        default="",
        help="Optional consensus round id to enforce at checkpoint.",
    )
    parser.add_argument(
        "--consensus-proposal-id",
        default="",
        help="Optional proposal id for consensus gate (blank means all proposals).",
    )
    parser.add_argument(
        "--consensus-required-decision",
        choices=["accepted", "rejected", "pending"],
        default="accepted",
        help="Required decision for consensus gate.",
    )
    parser.add_argument(
        "--consensus-finalize",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Whether checkpoint consensus gate should finalize round before evaluation.",
    )
    parser.add_argument(
        "--consensus-auto-create-round",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Auto-create consensus round/proposal when round id is not provided.",
    )
    parser.add_argument(
        "--consensus-participants",
        default="",
        help="Comma-separated participants used for auto-create consensus.",
    )
    parser.add_argument(
        "--consensus-proposal-author",
        default="planner",
        help="Proposal author for auto-created consensus proposal.",
    )
    parser.add_argument(
        "--consensus-proposal-content",
        default="auto-generated-proposal",
        help="Proposal content for auto-created consensus proposal.",
    )
    parser.add_argument(
        "--consensus-auto-vote-mode",
        choices=["none", "approve-all", "reject-all"],
        default="approve-all",
        help="Synthetic vote mode for auto-created consensus rounds.",
    )
    parser.add_argument(
        "--consensus-auto-vote-confidence",
        type=float,
        default=1.0,
        help="Confidence used by synthetic auto votes (0.0-1.0).",
    )
    parser.add_argument(
        "--project-root",
        default=".",
        help="Project root where .plan-executor artifacts are stored (default: current directory).",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output file path. Defaults to <project-root>/.plan-executor/runbooks/runbook-<timestamp>.json",
    )
    parser.add_argument(
        "--ai-worker-skip-warn-streak",
        type=int,
        default=2,
        help="Warn when ai-worker unavailable-skip streak reaches this count (0 disables).",
    )
    parser.add_argument(
        "--ai-worker-skip-fail-streak",
        type=int,
        default=0,
        help="Fail run when ai-worker unavailable-skip streak reaches this count (0 disables).",
    )
    parser.add_argument(
        "--guardrail-profile",
        choices=["auto"] + known_guardrail_profiles(),
        default="auto",
        help="Command guardrail profile. 'auto' maps from execution profile.",
    )
    parser.add_argument(
        "--guardrail-os-template",
        choices=known_os_templates(),
        default="auto",
        help="OS deny template for command guardrails.",
    )
    parser.add_argument(
        "--guardrail-mode",
        choices=["auto", "enforce", "audit", "human-approval"],
        default="auto",
        help="Guardrail mode override. 'auto' follows profile default.",
    )
    parser.add_argument(
        "--guardrail-allowlist-pattern",
        action="append",
        default=[],
        help="Additional allowlist regex pattern (repeatable).",
    )
    parser.add_argument(
        "--guardrail-denylist-pattern",
        action="append",
        default=[],
        help="Additional denylist regex pattern (repeatable).",
    )
    parser.add_argument(
        "--guardrail-include-os-risky-denylist",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include built-in OS risky command deny patterns (default: true).",
    )
    parser.add_argument(
        "--guardrail-environment",
        choices=["auto", "dev", "ci", "prod"],
        default="auto",
        help="Guardrail environment override (used by environment policy resolution).",
    )
    parser.add_argument(
        "--guardrail-role-policy",
        action="append",
        default=[],
        help="Role mode override row: <role>:<mode> where mode is enforce|audit|human-approval (repeatable).",
    )
    parser.add_argument(
        "--guardrail-code-intel",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable post-lane code change intelligence checks.",
    )
    parser.add_argument(
        "--guardrail-code-intel-mode",
        choices=["audit", "enforce"],
        default="audit",
        help="Code intelligence mode when enabled.",
    )
    parser.add_argument(
        "--guardrail-code-intel-max-total-code-files",
        type=int,
        default=25,
        help="Maximum touched code files before code intelligence violation.",
    )
    parser.add_argument(
        "--guardrail-code-intel-max-high-risk-files",
        type=int,
        default=3,
        help="Maximum high-risk touched code files before code intelligence violation.",
    )
    return parser.parse_args()


def build_nodes(lane_ids: List[str], mode: str) -> List[dict]:
    if mode == "sequential":
        nodes = []
        for i, lane_id in enumerate(lane_ids):
            depends = [lane_ids[i - 1]] if i > 0 else []
            nodes.append({"id": lane_id, "depends_on": depends})
        return nodes

    # Parallel default: lane-1 is prep lane, others depend on lane-1.
    nodes = []
    for i, lane_id in enumerate(lane_ids):
        depends = [] if i == 0 else [lane_ids[0]]
        nodes.append({"id": lane_id, "depends_on": depends})
    return nodes


def build_lane_cards(lane_ids: List[str], roles: List[str], task_type: str) -> List[dict]:
    cards = []
    for i, lane_id in enumerate(lane_ids):
        role = roles[i % len(roles)]
        cards.append(
            {
                "id": lane_id,
                "owner_role": role,
                "scope": f"{task_type} lane work for {lane_id}",
                "input_artifacts": [],
                "output_contract": {
                    "files_changed": [],
                    "acceptance": [],
                },
                "done_criteria": ["checkpoint accepted by integrator"],
            }
        )
    return cards


def main() -> int:
    args = parse_args()
    if args.lanes < 1 or args.lanes > 4:
        print("[ERROR] --lanes must be between 1 and 4.")
        return 2

    profile = PROFILES[args.profile]
    default_guardrail_profile = PROFILE_TO_GUARDRAIL.get(args.profile, "ci")
    guardrail_profile = default_guardrail_profile if args.guardrail_profile == "auto" else args.guardrail_profile
    guardrail_mode = "" if args.guardrail_mode == "auto" else args.guardrail_mode
    guardrail_environment = PROFILE_TO_ENVIRONMENT.get(args.profile, "ci") if args.guardrail_environment == "auto" else args.guardrail_environment
    role_policies = parse_role_policy_rows(list(args.guardrail_role_policy))
    capped_lanes = min(args.lanes, profile["max_lanes"])
    if capped_lanes != args.lanes:
        print(f"[WARN] profile '{args.profile}' caps lanes at {profile['max_lanes']}. Using {capped_lanes}.")

    lane_ids = [f"lane-{i}" for i in range(1, capped_lanes + 1)]
    preset = PRESETS[args.preset]
    nodes = build_nodes(lane_ids, args.mode)
    cards = build_lane_cards(lane_ids, preset["roles"], args.task_type)
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    default_parallel = 1 if args.mode == "sequential" else min(4, capped_lanes)
    max_parallel_workers = args.max_parallel_workers if args.max_parallel_workers > 0 else default_parallel
    if max_parallel_workers < 1:
        max_parallel_workers = 1

    project_root = Path(args.project_root).resolve()
    output_path = Path(args.output).resolve() if args.output else project_root / ".plan-executor" / "runbooks" / f"runbook-{now}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    checkpoint: Dict[str, object] = {
        "id": "checkpoint-1",
        "after_lanes": lane_ids,
        "gate_criteria": profile["gates"][args.task_type],
        "gate_commands": args.gate_cmd,
    }
    if args.consensus_round_id.strip() or bool(args.consensus_auto_create_round):
        participants = [x.strip() for x in args.consensus_participants.split(",") if x.strip()]
        checkpoint["consensus_gate"] = {
            "round_id": args.consensus_round_id.strip(),
            "proposal_id": args.consensus_proposal_id.strip(),
            "required_decision": args.consensus_required_decision,
            "finalize": bool(args.consensus_finalize),
            "auto_create_round": bool(args.consensus_auto_create_round),
            "participants": participants,
            "proposal_author": str(args.consensus_proposal_author).strip() or "planner",
            "proposal_content": str(args.consensus_proposal_content).strip() or "auto-generated-proposal",
            "auto_vote_mode": args.consensus_auto_vote_mode,
            "auto_vote_confidence": float(args.consensus_auto_vote_confidence),
        }

    runbook = {
        "meta": {
            "generated_at_utc": now,
            "preset": args.preset,
            "paper_basis": preset["paper_basis"],
            "profile": args.profile,
            "guardrail_profile": guardrail_profile,
            "environment": guardrail_environment,
            "mode": args.mode,
            "task_type": args.task_type,
            "max_parallel_workers": max_parallel_workers,
        },
        "team": {
            "orchestrator": "enabled",
            "integrator": "enabled",
            "lane_roles": preset["roles"],
        },
        "dag": {"nodes": nodes},
        "lanes": cards,
        "checkpoints": [checkpoint],
        "limits": {
            "max_replan": 3,
            "stall_rounds_threshold": 2,
            "merge_conflicts_threshold": 2,
            "verification_pass_rate_min": 0.7,
            "ai_worker_skip_warn_streak": max(0, args.ai_worker_skip_warn_streak),
            "ai_worker_skip_fail_streak": max(0, args.ai_worker_skip_fail_streak),
            "command_guardrails": {
                "enabled": True,
                "profile": guardrail_profile,
                "environment": guardrail_environment,
                "os_template": args.guardrail_os_template,
                "phases": ["lane", "gate"],
                "include_os_risky_denylist": bool(args.guardrail_include_os_risky_denylist),
                "allowlist_patterns": [str(x).strip() for x in args.guardrail_allowlist_pattern if str(x).strip()],
                "denylist_patterns": [str(x).strip() for x in args.guardrail_denylist_pattern if str(x).strip()],
                "role_policies": role_policies,
                "code_intelligence": {
                    "enabled": bool(args.guardrail_code_intel),
                    "mode": str(args.guardrail_code_intel_mode).strip().lower(),
                    "max_total_code_files": max(1, int(args.guardrail_code_intel_max_total_code_files)),
                    "max_high_risk_files": max(0, int(args.guardrail_code_intel_max_high_risk_files)),
                },
            },
        },
        "hooks": ["preflight", "lane_start", "lane_done", "checkpoint", "post_merge", "finalize"],
    }
    if guardrail_mode:
        runbook["limits"]["command_guardrails"]["mode"] = guardrail_mode

    output_path.write_text(json.dumps(runbook, indent=2), encoding="utf-8")
    print(f"[OK] Runbook written: {output_path}")
    print(
        "[OK] preset={preset} profile={profile} guardrail_profile={gprofile} guardrail_os={gos} "
        "guardrail_env={genv} mode={mode} lanes={lanes} task_type={task}".format(
            preset=args.preset,
            profile=args.profile,
            gprofile=guardrail_profile,
            gos=args.guardrail_os_template,
            genv=guardrail_environment,
            mode=args.mode,
            lanes=capped_lanes,
            task=args.task_type,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
