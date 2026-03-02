#!/usr/bin/env python3
"""Fixed benchmark runner for plan-executor runtime.

Runs a deterministic suite of regression scenarios and computes a score.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class BenchCase:
    case_id: str
    command: List[str]
    objective: str
    capability: str
    weight: float
    target_sec: float
    critical: bool = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run fixed runtime benchmark suite and score results.")
    parser.add_argument("--project-root", default=".", help="Project root containing .plan-executor")
    parser.add_argument("--include-ai-worker", action="store_true", help="Include ai_worker_regression_test in suite")
    parser.add_argument("--case-timeout-sec", type=int, default=300, help="Per-case timeout")
    parser.add_argument("--output-json", default="", help="Benchmark JSON output path")
    parser.add_argument("--output-md", default="", help="Benchmark Markdown output path")
    parser.add_argument("--baseline", default="", help="Optional prior benchmark JSON for delta")
    return parser.parse_args()


def build_suite(project_root: Path, include_ai_worker: bool) -> List[BenchCase]:
    scripts_root = Path(__file__).resolve().parent
    py = sys.executable

    def cmd(script_name: str) -> List[str]:
        return [py, str(scripts_root / script_name), "--project-root", str(project_root)]

    suite = [
        BenchCase(
            case_id="frontstage_codex_teams_regression",
            command=cmd("frontstage_codex_teams_regression_test.py"),
            objective="Frontstage multi-role planning and debate loop",
            capability="planning-consensus",
            weight=1.4,
            target_sec=6.0,
            critical=True,
        ),
        BenchCase(
            case_id="consensus_checkpoint_regression",
            command=cmd("consensus_checkpoint_regression_test.py"),
            objective="Checkpoint consensus gate pass/fail correctness",
            capability="consensus-gate",
            weight=1.2,
            target_sec=4.0,
            critical=True,
        ),
        BenchCase(
            case_id="consensus_autocreate_regression",
            command=cmd("consensus_autocreate_regression_test.py"),
            objective="Auto-create round/proposal/vote pipeline",
            capability="consensus-autocreate",
            weight=1.0,
            target_sec=4.0,
            critical=True,
        ),
        BenchCase(
            case_id="consensus_template_regression",
            command=cmd("consensus_template_regression_test.py"),
            objective="Template-backed synthetic vote path",
            capability="consensus-template",
            weight=1.0,
            target_sec=4.0,
        ),
        BenchCase(
            case_id="consensus_reconfigure_regression",
            command=cmd("consensus_reconfigure_regression_test.py"),
            objective="Mid-run consensus policy reconfigure over IPC",
            capability="ipc-control-consensus",
            weight=1.2,
            target_sec=5.0,
            critical=True,
        ),
        BenchCase(
            case_id="ipc_control_regression",
            command=cmd("ipc_control_regression_test.py"),
            objective="Mid-run replan via IPC + file logging",
            capability="ipc-control-replan",
            weight=1.0,
            target_sec=5.0,
        ),
        BenchCase(
            case_id="plan_search_regression",
            command=cmd("plan_search_regression_test.py"),
            objective="Candidate-plan scoring and best-plan selection",
            capability="plan-search",
            weight=1.2,
            target_sec=6.0,
            critical=True,
        ),
        BenchCase(
            case_id="delegate_worker_regression",
            command=cmd("delegate_worker_regression_test.py"),
            objective="Separated delegate worker queue execution",
            capability="delegate-worker",
            weight=1.0,
            target_sec=6.0,
        ),
        BenchCase(
            case_id="guardrails_regression",
            command=cmd("guardrails_regression_test.py"),
            objective="Command allow/deny guardrail enforcement",
            capability="command-guardrails",
            weight=1.1,
            target_sec=4.0,
            critical=True,
        ),
        BenchCase(
            case_id="code_intelligence_regression",
            command=cmd("code_intelligence_regression_test.py"),
            objective="AST-based code change impact analysis and enforce/audit behavior",
            capability="code-intelligence",
            weight=1.0,
            target_sec=5.0,
            critical=True,
        ),
        BenchCase(
            case_id="runbook_lint_regression",
            command=cmd("runbook_lint_regression_test.py"),
            objective="Guardrail-required runbook lint and runtime start gate",
            capability="runbook-lint",
            weight=1.0,
            target_sec=4.0,
            critical=True,
        ),
        BenchCase(
            case_id="entrypoint_lint_regression",
            command=cmd("entrypoint_lint_regression_test.py"),
            objective="Daemon enqueue and hybrid bridge enforce runbook lint policy",
            capability="entrypoint-lint",
            weight=1.0,
            target_sec=6.0,
            critical=True,
        ),
    ]

    if include_ai_worker:
        suite.append(
            BenchCase(
                case_id="ai_worker_regression",
                command=cmd("ai_worker_regression_test.py"),
                objective="AI worker routing/skip behavior",
                capability="ai-worker",
                weight=0.8,
                target_sec=8.0,
            )
        )
    return suite


def run_case(case: BenchCase, timeout_sec: int) -> Dict[str, Any]:
    started = time.perf_counter()
    ts_start = utc_now()
    try:
        proc = subprocess.run(
            case.command,
            capture_output=True,
            text=True,
            timeout=max(30, timeout_sec),
        )
        duration = time.perf_counter() - started
        passed = proc.returncode == 0
        return {
            "case_id": case.case_id,
            "objective": case.objective,
            "capability": case.capability,
            "critical": case.critical,
            "weight": case.weight,
            "target_sec": case.target_sec,
            "started_at": ts_start,
            "duration_sec": round(duration, 3),
            "returncode": int(proc.returncode),
            "status": "pass" if passed else "fail",
            "stdout_tail": (proc.stdout or "")[-2500:],
            "stderr_tail": (proc.stderr or "")[-1200:],
        }
    except subprocess.TimeoutExpired as exc:
        duration = time.perf_counter() - started
        return {
            "case_id": case.case_id,
            "objective": case.objective,
            "capability": case.capability,
            "critical": case.critical,
            "weight": case.weight,
            "target_sec": case.target_sec,
            "started_at": ts_start,
            "duration_sec": round(duration, 3),
            "returncode": 124,
            "status": "timeout",
            "stdout_tail": (exc.stdout or "")[-2500:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-1200:] if isinstance(exc.stderr, str) else "",
        }


def score_suite(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {
            "functionality_score": 0.0,
            "performance_score": 0.0,
            "critical_penalty": 0.0,
            "total_score": 0.0,
            "grade": "F",
            "pass_rate": 0.0,
            "critical_pass_rate": 0.0,
        }

    total_weight = sum(float(r.get("weight", 0.0)) for r in rows)
    total_weight = total_weight if total_weight > 0 else 1.0

    pass_weight = sum(float(r.get("weight", 0.0)) for r in rows if str(r.get("status", "")) == "pass")
    functionality_score = 80.0 * (pass_weight / total_weight)

    perf = 0.0
    for r in rows:
        if str(r.get("status", "")) != "pass":
            continue
        w = float(r.get("weight", 0.0))
        target = float(r.get("target_sec", 1.0))
        duration = max(0.001, float(r.get("duration_sec", target)))
        ratio = 1.0 if duration <= target else max(0.0, target / duration)
        perf += (20.0 * (w / total_weight)) * ratio
    performance_score = perf

    critical_rows = [r for r in rows if bool(r.get("critical", False))]
    critical_total = len(critical_rows)
    critical_pass = len([r for r in critical_rows if str(r.get("status", "")) == "pass"])
    critical_pass_rate = (critical_pass / critical_total) if critical_total > 0 else 1.0
    critical_penalty = 0.0
    if critical_pass_rate < 1.0:
        critical_penalty = 10.0 * (1.0 - critical_pass_rate)

    total_score = max(0.0, min(100.0, functionality_score + performance_score - critical_penalty))
    if total_score >= 95:
        grade = "A+"
    elif total_score >= 90:
        grade = "A"
    elif total_score >= 80:
        grade = "B"
    elif total_score >= 70:
        grade = "C"
    elif total_score >= 60:
        grade = "D"
    else:
        grade = "F"

    pass_rate = len([r for r in rows if str(r.get("status", "")) == "pass"]) / len(rows)
    return {
        "functionality_score": round(functionality_score, 2),
        "performance_score": round(performance_score, 2),
        "critical_penalty": round(critical_penalty, 2),
        "total_score": round(total_score, 2),
        "grade": grade,
        "pass_rate": round(pass_rate, 4),
        "critical_pass_rate": round(critical_pass_rate, 4),
    }


def read_baseline(path: Path) -> Dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def to_markdown(report: Dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = []
    lines.append("# Runtime Benchmark")
    lines.append("")
    lines.append(f"- ts: {report.get('ts', '')}")
    lines.append(f"- project_root: {report.get('project_root', '')}")
    lines.append(f"- suite_size: {len(report.get('cases', []))}")
    lines.append(f"- pass_rate: {summary.get('pass_rate', 0.0)}")
    lines.append(f"- critical_pass_rate: {summary.get('critical_pass_rate', 0.0)}")
    lines.append(f"- functionality_score: {summary.get('functionality_score', 0.0)}")
    lines.append(f"- performance_score: {summary.get('performance_score', 0.0)}")
    lines.append(f"- critical_penalty: {summary.get('critical_penalty', 0.0)}")
    lines.append(f"- total_score: {summary.get('total_score', 0.0)}")
    lines.append(f"- grade: {summary.get('grade', 'F')}")
    baseline = report.get("baseline_delta", {})
    if isinstance(baseline, dict) and baseline:
        lines.append(f"- baseline_total_score_delta: {baseline.get('total_score_delta', 0.0)}")
    lines.append("")
    lines.append("## Cases")
    for row in report.get("cases", []):
        lines.append(
            "- {case_id} status={status} rc={returncode} duration={duration_sec}s target={target_sec}s weight={weight} critical={critical}".format(
                **row
            )
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    pe_root = project_root / ".plan-executor"
    ts = utc_compact()
    suite = build_suite(project_root=project_root, include_ai_worker=bool(args.include_ai_worker))

    cases: List[Dict[str, Any]] = []
    started = time.perf_counter()
    print("Runtime Benchmark")
    print("=" * 40)
    for case in suite:
        row = run_case(case, timeout_sec=max(30, int(args.case_timeout_sec)))
        cases.append(row)
        print(
            f"[{row['status'].upper():7}] {case.case_id} "
            f"rc={row['returncode']} duration={row['duration_sec']}s target={row['target_sec']}s"
        )

    elapsed = round(time.perf_counter() - started, 3)
    summary = score_suite(cases)
    report: Dict[str, Any] = {
        "ts": utc_now(),
        "project_root": str(project_root),
        "elapsed_sec": elapsed,
        "suite_config": {
            "include_ai_worker": bool(args.include_ai_worker),
            "case_timeout_sec": int(args.case_timeout_sec),
        },
        "summary": summary,
        "cases": cases,
    }

    baseline_payload = None
    if args.baseline:
        baseline_payload = read_baseline(Path(args.baseline).resolve())
    latest_baseline = pe_root / "logs" / "benchmark-latest.json"
    if baseline_payload is None:
        baseline_payload = read_baseline(latest_baseline)

    if isinstance(baseline_payload, dict):
        prev_summary = baseline_payload.get("summary", {}) if isinstance(baseline_payload.get("summary", {}), dict) else {}
        report["baseline_delta"] = {
            "total_score_delta": round(float(summary.get("total_score", 0.0)) - float(prev_summary.get("total_score", 0.0)), 2),
            "pass_rate_delta": round(float(summary.get("pass_rate", 0.0)) - float(prev_summary.get("pass_rate", 0.0)), 4),
            "critical_pass_rate_delta": round(
                float(summary.get("critical_pass_rate", 0.0)) - float(prev_summary.get("critical_pass_rate", 0.0)),
                4,
            ),
        }

    out_json = (
        Path(args.output_json).resolve()
        if args.output_json
        else pe_root / "logs" / f"runtime-benchmark-{ts}.json"
    )
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")

    latest_baseline.parent.mkdir(parents=True, exist_ok=True)
    latest_baseline.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")

    if args.output_md:
        out_md = Path(args.output_md).resolve()
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(to_markdown(report), encoding="utf-8")
    else:
        default_md = pe_root / "logs" / f"runtime-benchmark-{ts}.md"
        default_md.write_text(to_markdown(report), encoding="utf-8")

    print("-" * 40)
    print(
        f"TOTAL {summary['total_score']}/100 grade={summary['grade']} "
        f"pass_rate={summary['pass_rate']} critical_pass_rate={summary['critical_pass_rate']}"
    )
    print(f"[OK] json={out_json}")
    print(f"[OK] latest={latest_baseline}")
    return 0 if float(summary.get("total_score", 0.0)) >= 70.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
