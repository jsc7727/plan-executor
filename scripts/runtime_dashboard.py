#!/usr/bin/env python3
"""Terminal dashboard for plan-executor runtime operations."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from runtime.daemon import RuntimeDaemon
from runtime.event_store import EventStore


def count_jsonl_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="plan-executor runtime dashboard")
    parser.add_argument("--project-root", default=".", help="Project root containing .plan-executor")
    parser.add_argument("--run-id", default="", help="Optional run id for detailed view")
    parser.add_argument("--events", type=int, default=10, help="Recent event count for detailed view")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    store = EventStore(project_root)
    daemon = RuntimeDaemon(project_root)

    run_ids = store.list_runs()
    statuses = Counter()
    runs = []
    ai_skip_total = 0
    ai_warning_total = 0
    replan_candidate_selected_total = 0
    consensus_gate_pass_total = 0
    consensus_gate_fail_total = 0
    consensus_reconfigured_total = 0
    consensus_reconfigure_noop_total = 0
    guardrail_lane_block_total = 0
    guardrail_gate_block_total = 0
    consensus_template_usage_total = 0
    for run_id in run_ids:
        state = store.read_state(run_id) or {}
        status = state.get("status", "unknown")
        statuses[status] += 1
        policy = state.get("ai_worker_policy", {}) if isinstance(state.get("ai_worker_policy", {}), dict) else {}
        ai_skip_total += int(policy.get("skip_total", 0))
        ai_warning_total += int(policy.get("warning_count", 0))
        events = store.read_events(run_id, limit=0)
        candidate_selected = sum(1 for e in events if str(e.get("event", "")) == "replan_candidate_selected")
        cp_pass = sum(
            1
            for e in events
            if str(e.get("event", "")) == "checkpoint"
            and "consensus-gate-pass" in [str(x).strip().lower() for x in e.get("payload", {}).get("evidence", [])]
        )
        cp_fail = sum(
            1
            for e in events
            if str(e.get("event", "")) == "checkpoint"
            and "consensus-gate-failed" in [str(x).strip().lower() for x in e.get("payload", {}).get("evidence", [])]
        )
        guardrail_gate_blocks = sum(
            1
            for e in events
            if str(e.get("event", "")) == "checkpoint"
            and "gate-command-guardrail-blocked" in [str(x).strip().lower() for x in e.get("payload", {}).get("evidence", [])]
        )
        guardrail_lane_blocks = sum(
            1
            for e in events
            if str(e.get("event", "")) == "lane_done"
            and "guardrail-blocked" in [str(x).strip().lower() for x in e.get("payload", {}).get("evidence", [])]
        )
        reconfigured = sum(1 for e in events if str(e.get("event", "")) == "consensus_reconfigured")
        reconfigure_noop = sum(1 for e in events if str(e.get("event", "")) == "consensus_reconfigure_noop")
        template_usage = sum(
            1
            for e in events
            if str(e.get("event", "")) == "checkpoint"
            and any(str(x).strip().lower().startswith("consensus-synthetic-template:") for x in e.get("payload", {}).get("evidence", []))
        )
        replan_candidate_selected_total += candidate_selected
        consensus_gate_pass_total += cp_pass
        consensus_gate_fail_total += cp_fail
        consensus_reconfigured_total += reconfigured
        consensus_reconfigure_noop_total += reconfigure_noop
        guardrail_lane_block_total += guardrail_lane_blocks
        guardrail_gate_block_total += guardrail_gate_blocks
        consensus_template_usage_total += template_usage
        runs.append(
            {
                "run_id": run_id,
                "status": status,
                "adapter": state.get("adapter", ""),
                "lane_count": len(state.get("lanes", [])),
                "updated_at": state.get("updated_at", ""),
                "ai_skip_total": int(policy.get("skip_total", 0)),
                "replan_candidate_selected_count": candidate_selected,
                "consensus_gate_pass_count": cp_pass,
                "consensus_gate_fail_count": cp_fail,
                "consensus_reconfigured_count": reconfigured,
                "consensus_reconfigure_noop_count": reconfigure_noop,
                "guardrail_lane_block_count": guardrail_lane_blocks,
                "guardrail_gate_block_count": guardrail_gate_blocks,
                "guardrail_block_count": guardrail_lane_blocks + guardrail_gate_blocks,
                "consensus_template_usage_count": template_usage,
            }
        )

    queue = daemon.queue_stats()
    pe_root = project_root / ".plan-executor"
    messages_root = pe_root / "messages"
    consensus_root = pe_root / "consensus"
    control_root = pe_root / "control" / "messages"
    messages_total = 0
    for fp in messages_root.glob("*.jsonl"):
        messages_total += count_jsonl_lines(fp)
    consensus_rounds_total = len(list(consensus_root.glob("*/*.json")))
    control_messages_total = 0
    for fp in control_root.glob("*.jsonl"):
        control_messages_total += count_jsonl_lines(fp)
    output = {
        "project_root": str(project_root),
        "runs_total": len(run_ids),
        "status_counts": dict(statuses),
        "queue": queue,
        "messages_total": messages_total,
        "consensus_rounds_total": consensus_rounds_total,
        "control_messages_total": control_messages_total,
        "ai_skip_total": ai_skip_total,
        "ai_warning_total": ai_warning_total,
        "replan_candidate_selected_total": replan_candidate_selected_total,
        "consensus_gate_pass_total": consensus_gate_pass_total,
        "consensus_gate_fail_total": consensus_gate_fail_total,
        "consensus_reconfigured_total": consensus_reconfigured_total,
        "consensus_reconfigure_noop_total": consensus_reconfigure_noop_total,
        "guardrail_lane_block_total": guardrail_lane_block_total,
        "guardrail_gate_block_total": guardrail_gate_block_total,
        "guardrail_block_total": guardrail_lane_block_total + guardrail_gate_block_total,
        "consensus_template_usage_total": consensus_template_usage_total,
        "runs": runs,
    }

    if args.run_id:
        state = store.read_state(args.run_id)
        if not state:
            print(f"[ERROR] run not found: {args.run_id}")
            return 1
        events = store.read_events(args.run_id, limit=max(1, args.events))
        output["selected_run"] = {
            "state": state,
            "events": events,
            "message_count": count_jsonl_lines(messages_root / f"{args.run_id}.jsonl"),
            "consensus_rounds": sorted([p.stem for p in (consensus_root / args.run_id).glob("*.json")]),
            "control_message_count": count_jsonl_lines(control_root / f"{args.run_id}.jsonl"),
            "ai_worker_policy": state.get("ai_worker_policy", {}),
            "plan_search_policy": state.get("plan_search_policy", {}),
        }

    if args.json:
        print(json.dumps(output, indent=2))
        return 0

    print(f"project={output['project_root']}")
    print(f"runs_total={output['runs_total']}")
    print(
        "status_counts="
        + ",".join([f"{k}:{v}" for k, v in sorted(output["status_counts"].items())])
        if output["status_counts"]
        else "status_counts=none"
    )
    print(
        f"queue pending={queue['pending']} processing={queue['processing']} "
        f"done={queue['done']} failed={queue['failed']}"
    )
    print(
        f"messages_total={messages_total} consensus_rounds_total={consensus_rounds_total} control_messages_total={control_messages_total} "
        f"ai_skip_total={ai_skip_total} ai_warning_total={ai_warning_total} "
        f"replan_candidate_selected_total={replan_candidate_selected_total} "
        f"consensus_gate_pass_total={consensus_gate_pass_total} consensus_gate_fail_total={consensus_gate_fail_total} "
        f"consensus_reconfigured_total={consensus_reconfigured_total} consensus_reconfigure_noop_total={consensus_reconfigure_noop_total} "
        f"guardrail_lane_block_total={guardrail_lane_block_total} guardrail_gate_block_total={guardrail_gate_block_total} "
        f"guardrail_block_total={guardrail_lane_block_total + guardrail_gate_block_total} "
        f"consensus_template_usage_total={consensus_template_usage_total}"
    )
    if runs:
        print("recent_runs:")
        for item in sorted(runs, key=lambda x: x.get("updated_at", ""), reverse=True)[:10]:
            print(
                f"  - {item['run_id']} status={item['status']} "
                f"adapter={item['adapter']} lanes={item['lane_count']} ai_skip_total={item['ai_skip_total']} "
                f"replan_candidates={item['replan_candidate_selected_count']} "
                f"consensus_pass={item['consensus_gate_pass_count']} consensus_fail={item['consensus_gate_fail_count']} "
                f"consensus_reconfigured={item['consensus_reconfigured_count']} "
                f"consensus_reconfigure_noop={item['consensus_reconfigure_noop_count']} "
                f"guardrail_blocks={item['guardrail_block_count']} "
                f"consensus_templates={item['consensus_template_usage_count']}"
            )

    if args.run_id and "selected_run" in output:
        print(f"selected_run={args.run_id}")
        state = output["selected_run"]["state"]
        print(f"  status={state.get('status')} adapter={state.get('adapter')} lanes={len(state.get('lanes', []))}")
        print(f"  recent_events={len(output['selected_run']['events'])}")
        print(
            f"  message_count={output['selected_run']['message_count']} "
            f"consensus_rounds={len(output['selected_run']['consensus_rounds'])} "
            f"control_messages={output['selected_run']['control_message_count']}"
        )
        print(f"  ai_worker_policy={output['selected_run']['ai_worker_policy']}")
        print(f"  plan_search_policy={output['selected_run']['plan_search_policy']}")
        for evt in output["selected_run"]["events"][-5:]:
            print(f"    * {evt.get('ts')} {evt.get('event')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
