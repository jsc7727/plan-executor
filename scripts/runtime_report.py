#!/usr/bin/env python3
"""Non-core reporting utilities for plan-executor runtime history."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from runtime.event_store import EventStore


def parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ",):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def duration_sec(state: Dict[str, Any]) -> float | None:
    start = parse_ts(str(state.get("created_at", "")))
    end = parse_ts(str(state.get("updated_at", "")))
    if not start or not end:
        return None
    return max(0.0, (end - start).total_seconds())


def count_jsonl_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def ai_metrics_from_events(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    ai_lane_total = 0
    ai_skip_count = 0
    engine_counts: Dict[str, Dict[str, int]] = {}
    lane_guardrail_blocks = 0
    gate_guardrail_blocks = 0

    for evt in events:
        event_name = str(evt.get("event", ""))
        if event_name == "checkpoint":
            evidence = [str(x).strip().lower() for x in evt.get("payload", {}).get("evidence", [])]
            if "gate-command-guardrail-blocked" in evidence:
                gate_guardrail_blocks += 1
        if event_name != "lane_done":
            continue
        payload = evt.get("payload", {})
        status = str(payload.get("status", ""))
        evidence = [str(x).strip().lower() for x in payload.get("evidence", [])]
        if "guardrail-blocked" in evidence:
            lane_guardrail_blocks += 1
        is_ai = any(x.startswith("engine:") for x in evidence) or ("ai-worker-execution" in evidence) or ("ai-worker-unavailable-skip" in evidence)
        if not is_ai:
            continue

        engine = "unknown"
        for token in evidence:
            if token.startswith("engine:"):
                engine = token.split(":", 1)[1].strip() or "unknown"
                break
        slot = engine_counts.setdefault(engine, {"total": 0, "success": 0, "skip": 0, "fail": 0})
        slot["total"] += 1
        ai_lane_total += 1
        if "ai-worker-unavailable-skip" in evidence:
            slot["skip"] += 1
            ai_skip_count += 1
        elif status == "pass":
            slot["success"] += 1
        else:
            slot["fail"] += 1

    engine_rates: Dict[str, Dict[str, Any]] = {}
    for engine, counts in engine_counts.items():
        total = counts["total"]
        effective_total = max(1, total - counts["skip"])
        engine_rates[engine] = {
            **counts,
            "success_rate": round((counts["success"] / total) if total else 0.0, 4),
            "success_rate_excluding_skips": round(counts["success"] / effective_total, 4),
        }

    template_counter: Counter[str] = Counter()
    for evt in events:
        if str(evt.get("event", "")) != "checkpoint":
            continue
        evidence = [str(x).strip().lower() for x in evt.get("payload", {}).get("evidence", [])]
        for token in evidence:
            if token.startswith("consensus-synthetic-template:"):
                template_counter[token.split(":", 1)[1].strip() or "unknown"] += 1

    return {
        "ai_lane_total": ai_lane_total,
        "ai_skip_count": ai_skip_count,
        "ai_skip_rate": round((ai_skip_count / ai_lane_total) if ai_lane_total else 0.0, 4),
        "ai_engine_stats": engine_rates,
        "replan_applied_count": sum(1 for e in events if str(e.get("event", "")) == "replan_applied"),
        "replan_candidate_selected_count": sum(1 for e in events if str(e.get("event", "")) == "replan_candidate_selected"),
        "control_message_received_count": sum(1 for e in events if str(e.get("event", "")) == "control_message_received"),
        "consensus_gate_pass_count": sum(
            1
            for e in events
            if str(e.get("event", "")) == "checkpoint"
            and "consensus-gate-pass" in [str(x).strip().lower() for x in e.get("payload", {}).get("evidence", [])]
        ),
        "consensus_gate_fail_count": sum(
            1
            for e in events
            if str(e.get("event", "")) == "checkpoint"
            and "consensus-gate-failed" in [str(x).strip().lower() for x in e.get("payload", {}).get("evidence", [])]
        ),
        "consensus_reconfigured_count": sum(1 for e in events if str(e.get("event", "")) == "consensus_reconfigured"),
        "consensus_reconfigure_noop_count": sum(1 for e in events if str(e.get("event", "")) == "consensus_reconfigure_noop"),
        "guardrail_lane_block_count": lane_guardrail_blocks,
        "guardrail_gate_block_count": gate_guardrail_blocks,
        "guardrail_block_count": lane_guardrail_blocks + gate_guardrail_blocks,
        "consensus_template_usage_count": int(sum(template_counter.values())),
        "consensus_template_usage": dict(template_counter),
    }


def summarize_runs(store: EventStore, run_id: str, event_limit: int, project_root: Path) -> Dict[str, Any]:
    run_ids = [run_id] if run_id else store.list_runs()
    runs: List[Dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    adapter_counts: Counter[str] = Counter()
    pe_root = project_root / ".plan-executor"
    messages_root = pe_root / "messages"
    consensus_root = pe_root / "consensus"
    control_root = pe_root / "control" / "messages"
    all_ai_lane_total = 0
    all_ai_skip_count = 0
    all_replan_applied = 0
    all_replan_candidate_selected = 0
    all_control_messages = 0
    all_consensus_gate_pass = 0
    all_consensus_gate_fail = 0
    all_consensus_reconfigured = 0
    all_consensus_reconfigure_noop = 0
    all_guardrail_lane_blocks = 0
    all_guardrail_gate_blocks = 0
    all_consensus_template_usage: Counter[str] = Counter()
    all_engine_counts: Dict[str, Dict[str, int]] = {}

    for rid in run_ids:
        state = store.read_state(rid)
        if not state:
            continue
        status = str(state.get("status", "unknown"))
        adapter = str(state.get("adapter", "unknown"))
        status_counts[status] += 1
        adapter_counts[adapter] += 1
        events = store.read_events(rid, limit=event_limit)
        all_events = store.read_events(rid, limit=0)
        ai_metrics = ai_metrics_from_events(all_events)
        all_ai_lane_total += int(ai_metrics["ai_lane_total"])
        all_ai_skip_count += int(ai_metrics["ai_skip_count"])
        all_replan_applied += int(ai_metrics["replan_applied_count"])
        all_replan_candidate_selected += int(ai_metrics["replan_candidate_selected_count"])
        all_control_messages += int(ai_metrics["control_message_received_count"])
        all_consensus_gate_pass += int(ai_metrics["consensus_gate_pass_count"])
        all_consensus_gate_fail += int(ai_metrics["consensus_gate_fail_count"])
        all_consensus_reconfigured += int(ai_metrics["consensus_reconfigured_count"])
        all_consensus_reconfigure_noop += int(ai_metrics["consensus_reconfigure_noop_count"])
        all_guardrail_lane_blocks += int(ai_metrics["guardrail_lane_block_count"])
        all_guardrail_gate_blocks += int(ai_metrics["guardrail_gate_block_count"])
        all_consensus_template_usage.update(dict(ai_metrics.get("consensus_template_usage", {})))
        for engine, row in ai_metrics["ai_engine_stats"].items():
            slot = all_engine_counts.setdefault(engine, {"total": 0, "success": 0, "skip": 0, "fail": 0})
            slot["total"] += int(row.get("total", 0))
            slot["success"] += int(row.get("success", 0))
            slot["skip"] += int(row.get("skip", 0))
            slot["fail"] += int(row.get("fail", 0))
        runs.append(
            {
                "run_id": rid,
                "status": status,
                "adapter": adapter,
                "lane_count": len(state.get("lanes", [])),
                "duration_sec": duration_sec(state),
                "updated_at": state.get("updated_at", ""),
                "event_count_sampled": len(events),
                "message_count": count_jsonl_lines(messages_root / f"{rid}.jsonl"),
                "consensus_rounds": len(list((consensus_root / rid).glob("*.json"))),
                "control_message_count": count_jsonl_lines(control_root / f"{rid}.jsonl"),
                "ai_lane_total": ai_metrics["ai_lane_total"],
                "ai_skip_count": ai_metrics["ai_skip_count"],
                "ai_skip_rate": ai_metrics["ai_skip_rate"],
                "ai_engine_stats": ai_metrics["ai_engine_stats"],
                "replan_applied_count": ai_metrics["replan_applied_count"],
                "replan_candidate_selected_count": ai_metrics["replan_candidate_selected_count"],
                "control_message_received_count": ai_metrics["control_message_received_count"],
                "consensus_gate_pass_count": ai_metrics["consensus_gate_pass_count"],
                "consensus_gate_fail_count": ai_metrics["consensus_gate_fail_count"],
                "consensus_reconfigured_count": ai_metrics["consensus_reconfigured_count"],
                "consensus_reconfigure_noop_count": ai_metrics["consensus_reconfigure_noop_count"],
                "guardrail_lane_block_count": ai_metrics["guardrail_lane_block_count"],
                "guardrail_gate_block_count": ai_metrics["guardrail_gate_block_count"],
                "guardrail_block_count": ai_metrics["guardrail_block_count"],
                "consensus_template_usage_count": ai_metrics["consensus_template_usage_count"],
                "consensus_template_usage": ai_metrics["consensus_template_usage"],
            }
        )

    runs_total = len(runs)
    completed = status_counts.get("completed", 0)
    success_rate = (completed / runs_total) if runs_total else 0.0
    engine_summary: Dict[str, Dict[str, Any]] = {}
    for engine, counts in all_engine_counts.items():
        total = counts["total"]
        effective_total = max(1, total - counts["skip"])
        engine_summary[engine] = {
            **counts,
            "success_rate": round((counts["success"] / total) if total else 0.0, 4),
            "success_rate_excluding_skips": round((counts["success"] / effective_total), 4),
        }

    return {
        "runs_total": runs_total,
        "success_rate": round(success_rate, 4),
        "status_counts": dict(status_counts),
        "adapter_counts": dict(adapter_counts),
        "messages_total": sum(r.get("message_count", 0) for r in runs),
        "consensus_rounds_total": sum(r.get("consensus_rounds", 0) for r in runs),
        "control_messages_total": sum(r.get("control_message_count", 0) for r in runs),
        "ai_skip_total": all_ai_skip_count,
        "ai_skip_rate": round((all_ai_skip_count / all_ai_lane_total) if all_ai_lane_total else 0.0, 4),
        "ai_engine_success_rates": engine_summary,
        "replan_applied_total": all_replan_applied,
        "replan_candidate_selected_total": all_replan_candidate_selected,
        "control_message_received_total": all_control_messages,
        "consensus_gate_pass_total": all_consensus_gate_pass,
        "consensus_gate_fail_total": all_consensus_gate_fail,
        "consensus_reconfigured_total": all_consensus_reconfigured,
        "consensus_reconfigure_noop_total": all_consensus_reconfigure_noop,
        "guardrail_lane_block_total": all_guardrail_lane_blocks,
        "guardrail_gate_block_total": all_guardrail_gate_blocks,
        "guardrail_block_total": all_guardrail_lane_blocks + all_guardrail_gate_blocks,
        "consensus_template_usage_total": int(sum(all_consensus_template_usage.values())),
        "consensus_template_usage": dict(all_consensus_template_usage),
        "runs": sorted(runs, key=lambda x: str(x.get("updated_at", "")), reverse=True),
    }


def to_markdown(report: Dict[str, Any]) -> str:
    lines = []
    lines.append("# Plan Executor Runtime Report")
    lines.append("")
    lines.append(f"- runs_total: {report.get('runs_total', 0)}")
    lines.append(f"- success_rate: {report.get('success_rate', 0.0)}")
    lines.append(f"- status_counts: {report.get('status_counts', {})}")
    lines.append(f"- adapter_counts: {report.get('adapter_counts', {})}")
    lines.append(f"- messages_total: {report.get('messages_total', 0)}")
    lines.append(f"- consensus_rounds_total: {report.get('consensus_rounds_total', 0)}")
    lines.append(f"- control_messages_total: {report.get('control_messages_total', 0)}")
    lines.append(f"- ai_skip_total: {report.get('ai_skip_total', 0)}")
    lines.append(f"- ai_skip_rate: {report.get('ai_skip_rate', 0)}")
    lines.append(f"- ai_engine_success_rates: {report.get('ai_engine_success_rates', {})}")
    lines.append(f"- replan_applied_total: {report.get('replan_applied_total', 0)}")
    lines.append(f"- replan_candidate_selected_total: {report.get('replan_candidate_selected_total', 0)}")
    lines.append(f"- control_message_received_total: {report.get('control_message_received_total', 0)}")
    lines.append(f"- consensus_gate_pass_total: {report.get('consensus_gate_pass_total', 0)}")
    lines.append(f"- consensus_gate_fail_total: {report.get('consensus_gate_fail_total', 0)}")
    lines.append(f"- consensus_reconfigured_total: {report.get('consensus_reconfigured_total', 0)}")
    lines.append(f"- consensus_reconfigure_noop_total: {report.get('consensus_reconfigure_noop_total', 0)}")
    lines.append(f"- guardrail_lane_block_total: {report.get('guardrail_lane_block_total', 0)}")
    lines.append(f"- guardrail_gate_block_total: {report.get('guardrail_gate_block_total', 0)}")
    lines.append(f"- guardrail_block_total: {report.get('guardrail_block_total', 0)}")
    lines.append(f"- consensus_template_usage_total: {report.get('consensus_template_usage_total', 0)}")
    lines.append(f"- consensus_template_usage: {report.get('consensus_template_usage', {})}")
    lines.append("")
    lines.append("## Runs")
    for item in report.get("runs", []):
        lines.append(
            "- {run_id} status={status} adapter={adapter} lanes={lane_count} duration_sec={duration_sec} messages={message_count} consensus={consensus_rounds} control={control_message_count} replans={replan_applied_count} candidates={replan_candidate_selected_count} consensus_pass={consensus_gate_pass_count} consensus_fail={consensus_gate_fail_count} consensus_reconfigured={consensus_reconfigured_count} consensus_reconfigure_noop={consensus_reconfigure_noop_count} guardrail_blocks={guardrail_block_count} template_usage={consensus_template_usage_count}".format(
                **item
            )
        )
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate runtime report from .plan-executor artifacts.")
    parser.add_argument("--project-root", default=".", help="Project root containing .plan-executor")
    parser.add_argument("--run-id", default="", help="Optional specific run id")
    parser.add_argument("--events", type=int, default=50, help="Events sampled per run")
    parser.add_argument("--format", choices=["json", "md"], default="json")
    parser.add_argument("--output", default="", help="Optional output file path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    store = EventStore(project_root)
    report = summarize_runs(store, run_id=args.run_id, event_limit=max(1, args.events), project_root=project_root)

    if args.format == "md":
        payload = to_markdown(report)
    else:
        payload = json.dumps(report, indent=2)

    if args.output:
        out = Path(args.output).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8")
        print(f"[OK] report written: {out}")
        return 0

    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
