#!/usr/bin/env python3
"""Regression tests for advanced consensus policy controls."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from runtime.consensus_engine import (
    create_round,
    evaluate_round,
    finalize_round,
    submit_critique,
    submit_proposal,
    vote,
)


def utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run consensus regression scenarios.")
    parser.add_argument("--project-root", default=".", help="Project root containing .plan-executor")
    return parser.parse_args()


def case_veto_and_single_winner(project_root: Path) -> tuple[bool, str]:
    run_id = f"consensus-veto-{utc_compact()}"
    rnd = create_round(
        project_root=project_root,
        run_id=run_id,
        topic="merge strategy",
        participants=["planner", "architect", "qa"],
        threshold=0.6,
        reject_threshold=0.6,
        quorum_ratio=0.66,
        min_critiques=1,
        veto_roles=["qa"],
        required_roles=["architect", "qa"],
        role_weights={"planner": 1.0, "architect": 1.1, "qa": 1.3},
        min_approve_confidence=0.5,
        single_winner=True,
    )
    p1 = submit_proposal(project_root, run_id, rnd.round_id, "planner", "option-a")
    p2 = submit_proposal(project_root, run_id, rnd.round_id, "architect", "option-b")

    submit_critique(project_root, run_id, rnd.round_id, p1["proposal_id"], "architect", "risk in rollback")
    submit_critique(project_root, run_id, rnd.round_id, p2["proposal_id"], "qa", "acceptable with tests")

    vote(project_root, run_id, rnd.round_id, p1["proposal_id"], "planner", "approve", confidence=0.9, role="planner")
    vote(project_root, run_id, rnd.round_id, p1["proposal_id"], "architect", "approve", confidence=0.8, role="architect")
    vote(project_root, run_id, rnd.round_id, p1["proposal_id"], "qa", "reject", confidence=1.0, role="qa")

    vote(project_root, run_id, rnd.round_id, p2["proposal_id"], "planner", "approve", confidence=0.9, role="planner")
    vote(project_root, run_id, rnd.round_id, p2["proposal_id"], "architect", "approve", confidence=0.9, role="architect")
    vote(project_root, run_id, rnd.round_id, p2["proposal_id"], "qa", "approve", confidence=0.7, role="qa")

    final = finalize_round(project_root, run_id, rnd.round_id)
    accepted = set(final.get("accepted", []))
    rejected = set(final.get("rejected", []))
    winner = str(final.get("winner", "")).strip()

    ok = (
        p2["proposal_id"] in accepted
        and p1["proposal_id"] in rejected
        and winner == p2["proposal_id"]
    )
    if not ok:
        return False, f"unexpected finalize result accepted={accepted} rejected={rejected} winner={winner}"

    eval_ok = evaluate_round(
        project_root=project_root,
        run_id=run_id,
        round_id=rnd.round_id,
        proposal_id=p2["proposal_id"],
        required_decision="accepted",
        finalize=False,
    )
    if not bool(eval_ok.get("pass", False)):
        return False, f"evaluate accepted failed: {eval_ok}"
    return True, f"run_id={run_id} round_id={rnd.round_id}"


def case_required_roles(project_root: Path) -> tuple[bool, str]:
    run_id = f"consensus-roles-{utc_compact()}"
    rnd = create_round(
        project_root=project_root,
        run_id=run_id,
        topic="required-role-check",
        participants=["planner", "architect", "qa"],
        threshold=0.6,
        quorum_ratio=0.5,
        required_roles=["qa"],
    )
    p1 = submit_proposal(project_root, run_id, rnd.round_id, "planner", "option-only")
    vote(project_root, run_id, rnd.round_id, p1["proposal_id"], "planner", "approve", confidence=1.0, role="planner")
    vote(project_root, run_id, rnd.round_id, p1["proposal_id"], "architect", "approve", confidence=1.0, role="architect")

    final = finalize_round(project_root, run_id, rnd.round_id)
    pending = set(final.get("pending", []))
    if p1["proposal_id"] not in pending:
        return False, f"expected pending due to missing required role, got {final}"
    return True, f"run_id={run_id} round_id={rnd.round_id}"


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    print("Consensus Regression Test")
    print("=" * 40)

    scenarios = [
        ("veto-and-single-winner", case_veto_and_single_winner),
        ("required-roles-enforced", case_required_roles),
    ]

    passed = 0
    for name, fn in scenarios:
        ok, detail = fn(project_root)
        if ok:
            passed += 1
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")

    print("-" * 40)
    print(f"RESULT: {passed}/{len(scenarios)} passed")
    return 0 if passed == len(scenarios) else 1


if __name__ == "__main__":
    raise SystemExit(main())

