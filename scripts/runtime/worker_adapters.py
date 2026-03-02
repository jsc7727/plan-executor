#!/usr/bin/env python3
"""Worker adapter abstraction for plan-executor runtime."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from .command_guardrails import resolve_command_guardrail, resolve_guardrail_policy_for_context
from .code_intelligence import analyze_code_change_impact, snapshot_git_changed_files
from .delegate_bus import create_request, wait_for_response


@dataclass
class LaneResult:
    lane_id: str
    status: str
    evidence: List[str]
    commands: List[Dict[str, Any]]
    error: str = ""


def _run_command(cmd: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        shell=True,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def _runtime_ctx(lane: Dict[str, Any], project_root: Path) -> Dict[str, Any]:
    ctx = lane.get("_runtime", {})
    run_id = str(ctx.get("run_id", "local-run"))
    lane_id = str(lane.get("id", "unknown-lane"))
    pe_root = project_root / ".plan-executor"
    return {
        "run_id": run_id,
        "lane_id": lane_id,
        "pe_root": pe_root,
        "logs_dir": pe_root / "logs",
        "worktrees_root": pe_root / "worktrees" / run_id,
    }


def _render_worker_template(template: str, lane: Dict[str, Any], cmd: str, ctx: Dict[str, Any]) -> str:
    return template.format(
        cmd=cmd,
        lane_id=str(lane.get("id", "")),
        owner_role=str(lane.get("owner_role", "")),
        run_id=str(ctx.get("run_id", "")),
        worker_id=str(ctx.get("worker_id", "")),
        worker_role=str(ctx.get("worker_role", "")),
    )


def _clip(text: str, n: int = 4000) -> str:
    return text[-n:] if text else ""


def _resolve_fallback_chain(lane: Dict[str, Any], primary_engine: str) -> List[str]:
    """Resolve the engine fallback chain for a lane.

    Reads from lane._runtime.fallback_chain or lane.fallback_chain.
    Default (no config): primary engine only — preserves skip behavior.
    With explicit config (e.g. "codex,gemini,shell"): tries engines in order.
    Returns list of engines to try IN ORDER (primary first, then fallbacks).
    """
    runtime = lane.get("_runtime", {})
    chain_raw = runtime.get("fallback_chain", lane.get("fallback_chain", ""))
    if isinstance(chain_raw, list) and chain_raw:
        chain = [str(x).strip().lower() for x in chain_raw if str(x).strip()]
    elif isinstance(chain_raw, str) and chain_raw.strip():
        chain = [x.strip().lower() for x in chain_raw.split(",") if x.strip()]
    else:
        # No fallback_chain configured: default to primary engine only (preserves skip behavior)
        chain = [primary_engine]

    # Ensure primary is first if not already
    if chain and chain[0] != primary_engine:
        chain = [primary_engine] + [e for e in chain if e != primary_engine]
    return chain


def _classify_failure(returncode: int, stderr: str, timed_out: bool) -> str:
    """Classify a command failure as 'infrastructure' or 'logic'.

    Infrastructure: timeout, signal kill, empty stderr (crash).
    Logic: nonzero exit with meaningful stderr (test/build/code error).
    """
    if timed_out:
        return "infrastructure"
    if returncode in (124, 125, 126, 127, 137, 139):
        # 124=timeout, 125=docker, 126=permission, 127=not-found, 137=SIGKILL, 139=SIGSEGV
        return "infrastructure"
    stderr_stripped = (stderr or "").strip()
    if not stderr_stripped and returncode != 0:
        return "infrastructure"
    return "logic"


def _sanitize_for_prompt(text: str) -> str:
    """Strip shell metacharacters from text embedded in a repair prompt.

    The repair prompt is passed through _render_worker_template and then to
    subprocess.run(shell=True).  Unsanitised stderr/stdout could inject
    arbitrary shell commands.  We replace dangerous metacharacters with
    safe placeholders.
    """
    replacements = {
        "`": "\uff40",
        "$(": "\uff04(",
        "$": "\uff04",
        "\\": "\uff3c",
        '"': "\u201c",
        "'": "\uff07",
        ";": "\uff1b",
        "|": "\uff5c",
        "&": "\uff06",
        "\n": " ",
    }
    out = text
    for old, new in replacements.items():
        out = out.replace(old, new)
    return out


def _build_repair_prompt(original_cmd: str, stderr: str, stdout: str, attempt: int, max_attempts: int) -> str:
    """Build a prompt asking the AI engine to diagnose and fix a logic failure."""
    safe_stderr = _sanitize_for_prompt(_clip(stderr, 2000))
    safe_stdout = _sanitize_for_prompt(_clip(stdout, 1000))
    safe_cmd = _sanitize_for_prompt(original_cmd)
    return (
        f"The following command failed (attempt {attempt}/{max_attempts}):\n"
        f"Command: {safe_cmd}\n"
        f"Stderr: {safe_stderr}\n"
        f"Stdout (tail): {safe_stdout}\n\n"
        f"Diagnose the error and run a corrected version of the command. "
        f"If the fix requires modifying code, make the minimal change needed and re-run."
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _probe_command(cmd: str, cwd: Path, timeout_sec: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        shell=True,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )


def _json_or_text(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {"kind": "empty", "value": ""}
    try:
        return {"kind": "json", "value": json.loads(raw)}
    except Exception:
        return {"kind": "text", "value": _clip(raw)}


def _artifact_path(ctx: Dict[str, Any]) -> Path:
    return Path(ctx["pe_root"]) / "artifacts" / str(ctx.get("run_id", "local-run")) / f"{str(ctx.get('lane_id', 'lane'))}.json"


def _write_artifact(ctx: Dict[str, Any], payload: Dict[str, Any]) -> str:
    path = _artifact_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return str(path)


def _guardrail_policy(lane: Dict[str, Any]) -> Dict[str, Any]:
    runtime = lane.get("_runtime", {}) if isinstance(lane.get("_runtime", {}), dict) else {}
    raw = runtime.get("command_guardrails", lane.get("command_guardrails", {}))
    role = str(lane.get("owner_role", "")).strip()
    env = str(runtime.get("guardrail_environment", "")).strip()
    return resolve_guardrail_policy_for_context(raw_policy=raw, role=role, environment=env)


def _guardrail_check(lane: Dict[str, Any], cmd: str, project_root: Path, phase: str = "lane") -> Dict[str, Any]:
    policy = _guardrail_policy(lane)
    runtime = lane.get("_runtime", {}) if isinstance(lane.get("_runtime", {}), dict) else {}
    out = resolve_command_guardrail(
        cmd=cmd,
        policy=policy,
        phase=phase,
        context={
            "project_root": str(project_root),
            "run_id": str(runtime.get("run_id", "")).strip(),
            "lane_id": str(runtime.get("lane_id", lane.get("id", ""))).strip(),
            "owner_role": str(lane.get("owner_role", "")).strip(),
            "phase": str(phase).strip().lower(),
        },
    )
    out["policy_enabled"] = bool(policy.get("enabled", False))
    return out


def _code_intel_baseline(lane: Dict[str, Any], project_root: Path) -> Set[str]:
    policy = _guardrail_policy(lane)
    code_intel = policy.get("code_intelligence", {})
    if not isinstance(code_intel, dict) or not bool(code_intel.get("enabled", False)):
        return set()
    snap = snapshot_git_changed_files(project_root)
    if not bool(snap.get("available", False)):
        return set()
    return {str(x).strip() for x in snap.get("files", []) if str(x).strip()}


def _apply_code_intelligence(
    lane: Dict[str, Any],
    project_root: Path,
    baseline_changed: Set[str],
    evidence: List[str],
    command_results: List[Dict[str, Any]],
) -> Tuple[bool, str]:
    policy = _guardrail_policy(lane)
    code_intel = policy.get("code_intelligence", {})
    if not isinstance(code_intel, dict) or not bool(code_intel.get("enabled", False)):
        return False, ""

    result = analyze_code_change_impact(
        project_root=project_root,
        baseline_changed_files=baseline_changed,
        policy=code_intel,
    )
    command_results.append(
        {
            "kind": "code-intelligence",
            "result": result,
        }
    )

    evidence.append("code-intel-enabled")
    evidence.append(f"code-intel-mode:{str(result.get('mode', 'audit'))}")
    evidence.append(f"code-intel-code-files:{int(result.get('code_file_count', 0))}")
    evidence.append(f"code-intel-high-risk:{int(result.get('high_risk_file_count', 0))}")
    if bool(result.get("violation", False)):
        evidence.append("code-intel-violation")
    if bool(result.get("applied", False)):
        evidence.append("code-intel-applied")
    else:
        evidence.append("code-intel-skipped")

    if bool(result.get("should_block", False)):
        return True, str(result.get("summary", "code-intelligence violation")).strip()
    return False, ""


class BaseAdapter:
    name = "base"

    def run_lane(self, lane: Dict[str, Any], project_root: Path) -> LaneResult:
        raise NotImplementedError


class InlineWorkerAdapter(BaseAdapter):
    name = "inline-worker"

    def run_lane(self, lane: Dict[str, Any], project_root: Path) -> LaneResult:
        lane_id = str(lane.get("id", "unknown-lane"))
        commands = lane.get("commands", [])
        if not commands:
            return LaneResult(
                lane_id=lane_id,
                status="pass",
                evidence=["contract-only-lane", "no-commands-provided"],
                commands=[],
            )

        baseline_changed = _code_intel_baseline(lane, project_root)
        command_results: List[Dict[str, Any]] = []
        guardrail_audit: List[str] = []
        for raw_cmd in commands:
            cmd = str(raw_cmd).strip()
            if not cmd:
                continue
            guard = _guardrail_check(lane, cmd, project_root, phase="lane")
            if bool(guard.get("audit_only", False)):
                guardrail_audit.append(f"guardrail-audit:{guard.get('reason', '')}")
            if not bool(guard.get("allowed", True)):
                command_results.append(
                    {
                        "cmd": cmd,
                        "returncode": 126,
                        "stdout": "",
                        "stderr": f"blocked by command guardrail: {guard.get('reason', '')}",
                        "cwd": str(project_root),
                        "guardrail": guard,
                    }
                )
                return LaneResult(
                    lane_id=lane_id,
                    status="fail",
                    evidence=[
                        "guardrail-blocked",
                        "inline-execution",
                        f"guardrail-reason:{guard.get('reason', '')}",
                    ],
                    commands=command_results,
                    error=f"command blocked by guardrail: {cmd}",
                )
            proc = _run_command(cmd, project_root)
            command_results.append(
                {
                    "cmd": cmd,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout[-4000:],
                    "stderr": proc.stderr[-4000:],
                    "cwd": str(project_root),
                }
            )
            if proc.returncode != 0:
                return LaneResult(
                    lane_id=lane_id,
                    status="fail",
                    evidence=["command-failed", "inline-execution"],
                    commands=command_results,
                    error=f"command failed: {cmd}",
                )

        evidence = ["commands-pass", "inline-execution"]
        for token in guardrail_audit[:5]:
            if token not in evidence:
                evidence.append(token)
        blocked_by_code_intel, code_intel_reason = _apply_code_intelligence(
            lane=lane,
            project_root=project_root,
            baseline_changed=baseline_changed,
            evidence=evidence,
            command_results=command_results,
        )
        if blocked_by_code_intel:
            return LaneResult(
                lane_id=lane_id,
                status="fail",
                evidence=evidence + ["code-intel-blocked", "inline-execution"],
                commands=command_results,
                error=f"code intelligence blocked lane: {code_intel_reason}",
            )
        return LaneResult(
            lane_id=lane_id,
            status="pass",
            evidence=evidence,
            commands=command_results,
        )


class WorktreeWorkerAdapter(InlineWorkerAdapter):
    name = "worktree-worker"

    def _prepare_worktree(self, lane: Dict[str, Any], project_root: Path) -> Tuple[Path | None, List[str], str]:
        notes: List[str] = []
        if not shutil.which("git"):
            return None, notes, "git unavailable for worktree adapter"

        # Require a git repository for worktree isolation.
        probe = _run_command("git rev-parse --show-toplevel", project_root)
        if probe.returncode != 0:
            return None, notes, "not a git repository; cannot create worktree"

        ctx = _runtime_ctx(lane, project_root)
        worktrees_root: Path = ctx["worktrees_root"]
        worktrees_root.mkdir(parents=True, exist_ok=True)
        worktree_dir = worktrees_root / str(ctx["lane_id"])
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)

        if not worktree_dir.exists():
            add_cmd = f'git worktree add --detach "{worktree_dir}" HEAD'
            add_proc = _run_command(add_cmd, project_root)
            if add_proc.returncode != 0:
                return None, notes, f"failed to create worktree: {add_proc.stderr.strip()[:300]}"
            notes.append("created-worktree")
        else:
            notes.append("reuse-worktree")

        return worktree_dir, notes, ""

    def run_lane(self, lane: Dict[str, Any], project_root: Path) -> LaneResult:
        lane_id = str(lane.get("id", "unknown-lane"))
        worktree_dir, notes, err = self._prepare_worktree(lane, project_root)
        if not worktree_dir:
            # Fallback to inline if worktree setup is impossible.
            base = super().run_lane(lane, project_root)
            base.evidence.extend(["worktree-fallback-inline", err])
            return base

        commands = lane.get("commands", [])
        if not commands:
            return LaneResult(
                lane_id=lane_id,
                status="pass",
                evidence=["contract-only-lane", "worktree-execution"] + notes,
                commands=[],
            )

        baseline_changed = _code_intel_baseline(lane, worktree_dir)
        command_results: List[Dict[str, Any]] = []
        guardrail_audit: List[str] = []
        for raw_cmd in commands:
            cmd = str(raw_cmd).strip()
            if not cmd:
                continue
            guard = _guardrail_check(lane, cmd, project_root, phase="lane")
            if bool(guard.get("audit_only", False)):
                guardrail_audit.append(f"guardrail-audit:{guard.get('reason', '')}")
            if not bool(guard.get("allowed", True)):
                command_results.append(
                    {
                        "cmd": cmd,
                        "returncode": 126,
                        "stdout": "",
                        "stderr": f"blocked by command guardrail: {guard.get('reason', '')}",
                        "cwd": str(worktree_dir),
                        "guardrail": guard,
                    }
                )
                return LaneResult(
                    lane_id=lane_id,
                    status="fail",
                    evidence=["guardrail-blocked", "worktree-execution"] + notes,
                    commands=command_results,
                    error=f"command blocked by guardrail: {cmd}",
                )
            proc = _run_command(cmd, worktree_dir)
            command_results.append(
                {
                    "cmd": cmd,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout[-4000:],
                    "stderr": proc.stderr[-4000:],
                    "cwd": str(worktree_dir),
                }
            )
            if proc.returncode != 0:
                return LaneResult(
                    lane_id=lane_id,
                    status="fail",
                    evidence=["command-failed", "worktree-execution"] + notes,
                    commands=command_results,
                    error=f"command failed in worktree: {cmd}",
                )

        evidence = ["commands-pass", "worktree-execution"] + notes
        for token in guardrail_audit[:5]:
            if token not in evidence:
                evidence.append(token)
        blocked_by_code_intel, code_intel_reason = _apply_code_intelligence(
            lane=lane,
            project_root=worktree_dir,
            baseline_changed=baseline_changed,
            evidence=evidence,
            command_results=command_results,
        )
        if blocked_by_code_intel:
            return LaneResult(
                lane_id=lane_id,
                status="fail",
                evidence=evidence + ["code-intel-blocked", "worktree-execution"] + notes,
                commands=command_results,
                error=f"code intelligence blocked lane: {code_intel_reason}",
            )
        return LaneResult(
            lane_id=lane_id,
            status="pass",
            evidence=evidence,
            commands=command_results,
        )


class TmuxWorkerAdapter(InlineWorkerAdapter):
    name = "tmux-worker"

    def _tmux_run(self, session: str, shell_cmd: str, timeout_sec: int = 180) -> Tuple[int, str]:
        # Launch detached session running shell_cmd and wait for completion.
        start = _run_command(f'tmux new-session -d -s "{session}" "{shell_cmd}"', Path.cwd())
        if start.returncode != 0:
            return 1, start.stderr.strip()[:500]

        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            alive = _run_command(f'tmux has-session -t "{session}"', Path.cwd())
            if alive.returncode != 0:
                # Session ended => command finished.
                break
            time.sleep(0.25)
        else:
            _run_command(f'tmux kill-session -t "{session}"', Path.cwd())
            return 124, "tmux session timeout"

        return 0, ""

    def run_lane(self, lane: Dict[str, Any], project_root: Path) -> LaneResult:
        lane_id = str(lane.get("id", "unknown-lane"))
        if not shutil.which("tmux"):
            base = super().run_lane(lane, project_root)
            base.evidence.append("tmux-fallback-inline")
            return base

        commands = lane.get("commands", [])
        if not commands:
            return LaneResult(
                lane_id=lane_id,
                status="pass",
                evidence=["contract-only-lane", "tmux-execution"],
                commands=[],
            )

        ctx = _runtime_ctx(lane, project_root)
        logs_dir: Path = ctx["logs_dir"]
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f"{ctx['run_id']}-{lane_id}-tmux.log"

        baseline_changed = _code_intel_baseline(lane, project_root)
        command_results: List[Dict[str, Any]] = []
        guardrail_audit: List[str] = []
        for i, raw_cmd in enumerate(commands, start=1):
            cmd = str(raw_cmd).strip()
            if not cmd:
                continue
            guard = _guardrail_check(lane, cmd, project_root, phase="lane")
            if bool(guard.get("audit_only", False)):
                guardrail_audit.append(f"guardrail-audit:{guard.get('reason', '')}")
            if not bool(guard.get("allowed", True)):
                command_results.append(
                    {
                        "cmd": cmd,
                        "returncode": 126,
                        "stdout": "",
                        "stderr": f"blocked by command guardrail: {guard.get('reason', '')}",
                        "log": str(log_path),
                        "cwd": str(project_root),
                        "guardrail": guard,
                    }
                )
                return LaneResult(
                    lane_id=lane_id,
                    status="fail",
                    evidence=["guardrail-blocked", "tmux-execution"],
                    commands=command_results,
                    error=f"command blocked by guardrail: {cmd}",
                )
            session = f"pe-{ctx['run_id'][:8]}-{lane_id[:12]}-{i}"
            # Use POSIX sh inside tmux. On non-POSIX tmux installs this will fail and fallback.
            wrapped = (
                "sh -lc '"
                + cmd.replace("'", "'\"'\"'")
                + f" >> \"{str(log_path).replace('\\', '/')}\" 2>&1'"
            )
            rc, err = self._tmux_run(session, wrapped)
            command_results.append(
                {
                    "cmd": cmd,
                    "returncode": rc,
                    "stdout": "",
                    "stderr": err,
                    "log": str(log_path),
                    "cwd": str(project_root),
                }
            )
            # Cleanup best-effort in case session still exists.
            _run_command(f'tmux kill-session -t "{session}"', Path.cwd())
            if rc != 0:
                if i == 1:
                    # Fast fallback when tmux execution is not viable in this environment.
                    base = super().run_lane(lane, project_root)
                    base.evidence.extend(["tmux-fallback-inline", "tmux-command-failed"])
                    base.commands.extend(command_results)
                    return base
                return LaneResult(
                    lane_id=lane_id,
                    status="fail",
                    evidence=["command-failed", "tmux-execution"],
                    commands=command_results,
                    error=f"tmux command failed: {cmd} ({err})",
                )

        evidence = ["commands-pass", "tmux-execution"]
        for token in guardrail_audit[:5]:
            if token not in evidence:
                evidence.append(token)
        blocked_by_code_intel, code_intel_reason = _apply_code_intelligence(
            lane=lane,
            project_root=project_root,
            baseline_changed=baseline_changed,
            evidence=evidence,
            command_results=command_results,
        )
        if blocked_by_code_intel:
            return LaneResult(
                lane_id=lane_id,
                status="fail",
                evidence=evidence + ["code-intel-blocked", "tmux-execution"],
                commands=command_results,
                error=f"code intelligence blocked lane: {code_intel_reason}",
            )
        return LaneResult(
            lane_id=lane_id,
            status="pass",
            evidence=evidence,
            commands=command_results,
        )


class ProcessWorkerAdapter(InlineWorkerAdapter):
    name = "process-worker"

    def run_lane(self, lane: Dict[str, Any], project_root: Path) -> LaneResult:
        lane_id = str(lane.get("id", "unknown-lane"))
        commands = lane.get("commands", [])
        if not commands:
            return LaneResult(
                lane_id=lane_id,
                status="pass",
                evidence=["contract-only-lane", "process-worker-execution"],
                commands=[],
            )

        ctx = _runtime_ctx(lane, project_root)
        runtime = lane.get("_runtime", {})
        template = str(runtime.get("worker_command_template", "")).strip() or str(lane.get("worker_command_template", "")).strip()
        if "{cmd}" not in template:
            template = "{cmd}" if not template else template + " {cmd}"

        worker_id = str(runtime.get("worker_id", "")).strip()
        worker_role = str(runtime.get("worker_role", "")).strip()

        baseline_changed = _code_intel_baseline(lane, project_root)
        command_results: List[Dict[str, Any]] = []
        guardrail_audit: List[str] = []
        for raw_cmd in commands:
            cmd = str(raw_cmd).strip()
            if not cmd:
                continue
            guard = _guardrail_check(lane, cmd, project_root, phase="lane")
            if bool(guard.get("audit_only", False)):
                guardrail_audit.append(f"guardrail-audit:{guard.get('reason', '')}")
            if not bool(guard.get("allowed", True)):
                command_results.append(
                    {
                        "cmd": cmd,
                        "wrapped_cmd": "",
                        "returncode": 126,
                        "stdout": "",
                        "stderr": f"blocked by command guardrail: {guard.get('reason', '')}",
                        "cwd": str(project_root),
                        "worker_id": worker_id,
                        "worker_role": worker_role,
                        "guardrail": guard,
                    }
                )
                return LaneResult(
                    lane_id=lane_id,
                    status="fail",
                    evidence=["guardrail-blocked", "process-worker-execution"],
                    commands=command_results,
                    error=f"command blocked by guardrail: {cmd}",
                )
            try:
                wrapped_cmd = _render_worker_template(template, lane, cmd, ctx)
            except Exception as exc:
                return LaneResult(
                    lane_id=lane_id,
                    status="fail",
                    evidence=["invalid-worker-template", "process-worker-execution"],
                    commands=command_results,
                    error=f"invalid worker command template: {exc}",
                )
            proc = _run_command(wrapped_cmd, project_root)
            command_results.append(
                {
                    "cmd": cmd,
                    "wrapped_cmd": wrapped_cmd,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout[-4000:],
                    "stderr": proc.stderr[-4000:],
                    "cwd": str(project_root),
                    "worker_id": worker_id,
                    "worker_role": worker_role,
                }
            )
            if proc.returncode != 0:
                return LaneResult(
                    lane_id=lane_id,
                    status="fail",
                    evidence=["command-failed", "process-worker-execution"],
                    commands=command_results,
                    error=f"process-worker command failed: {wrapped_cmd}",
                )

        evidence = ["commands-pass", "process-worker-execution"]
        for token in guardrail_audit[:5]:
            if token not in evidence:
                evidence.append(token)
        if worker_id:
            evidence.append(f"worker-id:{worker_id}")
        if worker_role:
            evidence.append(f"worker-role:{worker_role}")
        blocked_by_code_intel, code_intel_reason = _apply_code_intelligence(
            lane=lane,
            project_root=project_root,
            baseline_changed=baseline_changed,
            evidence=evidence,
            command_results=command_results,
        )
        if blocked_by_code_intel:
            return LaneResult(
                lane_id=lane_id,
                status="fail",
                evidence=evidence + ["code-intel-blocked", "process-worker-execution"],
                commands=command_results,
                error=f"code intelligence blocked lane: {code_intel_reason}",
            )
        return LaneResult(
            lane_id=lane_id,
            status="pass",
            evidence=evidence,
            commands=command_results,
        )


class AiCliWorkerAdapter(BaseAdapter):
    name = "ai-worker"

    def _detect_engine(self, lane: Dict[str, Any]) -> str:
        runtime = lane.get("_runtime", {})
        engine = str(runtime.get("worker_engine", "")).strip().lower() or str(lane.get("worker_engine", "")).strip().lower()
        if engine in {"codex", "gemini"}:
            return engine
        return "codex"

    def _default_template(self, engine: str) -> str:
        if engine == "gemini":
            return 'gemini -p "{cmd}" --yolo'
        return 'codex exec --enable multi_agent --skip-git-repo-check "{cmd}"'

    def _normalize_ai_result(
        self,
        ctx: Dict[str, Any],
        lane: Dict[str, Any],
        engine: str,
        worker_id: str,
        worker_role: str,
        status: str,
        reason: str,
        attempts: List[Dict[str, Any]],
    ) -> str:
        payload = {
            "schema_version": "ai-worker-v1",
            "produced_at": _utc_now(),
            "run_id": str(ctx.get("run_id", "")),
            "lane_id": str(ctx.get("lane_id", "")),
            "owner_role": str(lane.get("owner_role", "")),
            "worker": {
                "id": worker_id,
                "role": worker_role,
                "engine": engine,
            },
            "status": status,
            "reason": reason,
            "attempts": attempts,
        }
        return _write_artifact(ctx, payload)

    def _check_available(self, engine: str, project_root: Path) -> Tuple[bool, str, Dict[str, Any]]:
        if engine == "codex":
            if not shutil.which("codex"):
                return False, "codex-cli-not-found", {"check_cmd": "where codex", "returncode": 127, "stdout": "", "stderr": "not found"}
            try:
                proc = _probe_command("codex login status", project_root, timeout_sec=20)
            except Exception as exc:
                return False, "codex-login-check-error", {"check_cmd": "codex login status", "returncode": 1, "stdout": "", "stderr": str(exc)}
            detail = {
                "check_cmd": "codex login status",
                "returncode": proc.returncode,
                "stdout": _clip(proc.stdout),
                "stderr": _clip(proc.stderr),
            }
            if proc.returncode == 0:
                return True, "ok", detail
            return False, "codex-not-logged-in", detail

        if engine == "gemini":
            if not shutil.which("gemini"):
                return False, "gemini-cli-not-found", {"check_cmd": "where gemini", "returncode": 127, "stdout": "", "stderr": "not found"}
            api_key = os.environ.get("GEMINI_API_KEY", "").strip()
            if not api_key:
                return False, "gemini-not-logged-in-or-api-key-missing", {
                    "check_cmd": "env:GEMINI_API_KEY",
                    "returncode": 1,
                    "stdout": "",
                    "stderr": "GEMINI_API_KEY is empty",
                }
            return True, "ok", {"check_cmd": "env:GEMINI_API_KEY", "returncode": 0, "stdout": "set", "stderr": ""}

        return False, "unsupported-engine", {"check_cmd": "engine-validate", "returncode": 2, "stdout": "", "stderr": engine}

    def run_lane(self, lane: Dict[str, Any], project_root: Path) -> LaneResult:
        lane_id = str(lane.get("id", "unknown-lane"))
        commands = lane.get("commands", [])
        runtime = lane.get("_runtime", {})
        ctx = _runtime_ctx(lane, project_root)
        engine = self._detect_engine(lane)
        worker_id = str(runtime.get("worker_id", "")).strip()
        worker_role = str(runtime.get("worker_role", "")).strip()
        template = str(runtime.get("worker_command_template", "")).strip() or str(lane.get("worker_command_template", "")).strip()
        if not template:
            template = self._default_template(engine)
        if "{cmd}" not in template:
            template = template + " {cmd}"

        timeout_sec = int(runtime.get("ai_timeout_sec", lane.get("ai_timeout_sec", 180)))
        max_retries = int(runtime.get("ai_max_retries", lane.get("ai_max_retries", 1)))
        backoff_sec = float(runtime.get("ai_backoff_sec", lane.get("ai_backoff_sec", 1.5)))
        max_replan = int(runtime.get("max_replan", lane.get("max_replan", 2)))
        if timeout_sec < 10:
            timeout_sec = 10
        if max_retries < 0:
            max_retries = 0
        if backoff_sec < 0:
            backoff_sec = 0.0
        if max_replan < 0:
            max_replan = 0

        fallback_chain = _resolve_fallback_chain(lane, engine)
        fallback_attempts: List[Dict[str, Any]] = []
        effective_engine = engine
        available = False
        reason = ""
        check_detail: Dict[str, Any] = {}

        for candidate_engine in fallback_chain:
            if candidate_engine == "shell":
                # Shell fallback: delegate to InlineWorkerAdapter
                inline = InlineWorkerAdapter()
                result = inline.run_lane(lane, project_root)
                result.evidence.append("engine-fallback-to-shell")
                result.evidence.append(f"fallback-chain:{','.join(fallback_chain)}")
                for fa in fallback_attempts:
                    result.evidence.append(f"fallback-skip:{fa['engine']}:{fa['reason']}")
                return result

            available, reason, check_detail = self._check_available(candidate_engine, project_root)
            if available:
                effective_engine = candidate_engine
                if candidate_engine != engine:
                    # Fell back to a different engine; update template if it was the default
                    default_primary_template = self._default_template(engine)
                    if template == default_primary_template:
                        template = self._default_template(effective_engine)
                break
            else:
                fallback_attempts.append({
                    "engine": candidate_engine,
                    "reason": reason,
                    "detail": check_detail,
                })

        if not available:
            # All engines in chain exhausted (no shell entry or chain was empty)
            last_detail = check_detail
            artifact = self._normalize_ai_result(
                ctx=ctx,
                lane=lane,
                engine=engine,
                worker_id=worker_id,
                worker_role=worker_role,
                status="skip",
                reason=f"all-engines-unavailable:{','.join(fa['engine'] for fa in fallback_attempts)}",
                attempts=[
                    {
                        "attempt": i + 1,
                        "check_cmd": fa["detail"].get("check_cmd", ""),
                        "returncode": fa["detail"].get("returncode", 1),
                        "stdout": fa["detail"].get("stdout", ""),
                        "stderr": fa["detail"].get("stderr", ""),
                        "started_at": _utc_now(),
                        "ended_at": _utc_now(),
                        "timed_out": False,
                    }
                    for i, fa in enumerate(fallback_attempts)
                ],
            )
            return LaneResult(
                lane_id=lane_id,
                status="pass",
                evidence=[
                    "ai-worker-unavailable-skip",
                    f"engine:{engine}",
                    reason,
                    f"fallback-chain:{','.join(fallback_chain)}",
                    f"artifact-json:{artifact}",
                    f"worker-id:{worker_id}" if worker_id else "worker-id:none",
                ] + [f"fallback-skip:{fa['engine']}:{fa['reason']}" for fa in fallback_attempts],
                commands=[
                    {
                        "cmd": "",
                        "wrapped_cmd": "",
                        "returncode": last_detail.get("returncode", 1),
                        "stdout": last_detail.get("stdout", ""),
                        "stderr": last_detail.get("stderr", ""),
                        "engine": engine,
                        "check_cmd": last_detail.get("check_cmd", ""),
                        "worker_id": worker_id,
                        "worker_role": worker_role,
                    }
                ],
                error="",
            )

        # Add fallback evidence to indicate which engine is actually running
        fallback_evidence: List[str] = []
        if effective_engine != engine:
            fallback_evidence.append(f"engine-fallback:{engine}\u2192{effective_engine}")
        fallback_evidence.append(f"fallback-chain:{','.join(fallback_chain)}")

        if not commands:
            artifact = self._normalize_ai_result(
                ctx=ctx,
                lane=lane,
                engine=effective_engine,
                worker_id=worker_id,
                worker_role=worker_role,
                status="pass",
                reason="contract-only-lane",
                attempts=[],
            )
            return LaneResult(
                lane_id=lane_id,
                status="pass",
                evidence=[
                    "contract-only-lane",
                    "ai-worker-execution",
                    f"engine:{effective_engine}",
                    f"artifact-json:{artifact}",
                ] + fallback_evidence,
                commands=[],
            )

        baseline_changed = _code_intel_baseline(lane, project_root)
        command_results: List[Dict[str, Any]] = []
        guardrail_audit: List[str] = []
        for raw_cmd in commands:
            cmd = str(raw_cmd).strip()
            if not cmd:
                continue
            repair_attempts = 0
            guard = _guardrail_check(lane, cmd, project_root, phase="lane")
            if bool(guard.get("audit_only", False)):
                guardrail_audit.append(f"guardrail-audit:{guard.get('reason', '')}")
            if not bool(guard.get("allowed", True)):
                command_results.append(
                    {
                        "cmd": cmd,
                        "wrapped_cmd": "",
                        "attempt": 1,
                        "returncode": 126,
                        "timed_out": False,
                        "stdout": "",
                        "stderr": f"blocked by command guardrail: {guard.get('reason', '')}",
                        "stdout_parsed": _json_or_text(""),
                        "cwd": str(project_root),
                        "engine": effective_engine,
                        "worker_id": worker_id,
                        "worker_role": worker_role,
                        "started_at": _utc_now(),
                        "ended_at": _utc_now(),
                        "guardrail": guard,
                    }
                )
                artifact = self._normalize_ai_result(
                    ctx=ctx,
                    lane=lane,
                    engine=effective_engine,
                    worker_id=worker_id,
                    worker_role=worker_role,
                    status="fail",
                    reason="command-guardrail-blocked",
                    attempts=command_results,
                )
                return LaneResult(
                    lane_id=lane_id,
                    status="fail",
                    evidence=[
                        "guardrail-blocked",
                        "ai-worker-execution",
                        f"engine:{effective_engine}",
                        f"artifact-json:{artifact}",
                    ] + fallback_evidence,
                    commands=command_results,
                    error=f"ai-worker command blocked by guardrail: {cmd}",
                )
            try:
                wrapped_cmd = _render_worker_template(template, lane, cmd, ctx)
            except Exception as exc:
                artifact = self._normalize_ai_result(
                    ctx=ctx,
                    lane=lane,
                    engine=effective_engine,
                    worker_id=worker_id,
                    worker_role=worker_role,
                    status="fail",
                    reason="invalid-worker-template",
                    attempts=command_results,
                )
                return LaneResult(
                    lane_id=lane_id,
                    status="fail",
                    evidence=[
                        "invalid-worker-template",
                        "ai-worker-execution",
                        f"engine:{effective_engine}",
                        f"artifact-json:{artifact}",
                    ] + fallback_evidence,
                    commands=command_results,
                    error=f"invalid ai worker command template: {exc}",
                )

            succeeded = False
            total_tries = max_retries + 1
            for attempt in range(1, total_tries + 1):
                started_at = _utc_now()
                timed_out = False
                returncode = 1
                stdout = ""
                stderr = ""
                try:
                    proc = subprocess.run(
                        wrapped_cmd,
                        shell=True,
                        cwd=str(project_root),
                        capture_output=True,
                        text=True,
                        timeout=timeout_sec,
                    )
                    returncode = int(proc.returncode)
                    stdout = _clip(proc.stdout)
                    stderr = _clip(proc.stderr)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    returncode = 124
                    stderr = f"timeout>{timeout_sec}s"
                ended_at = _utc_now()

                command_results.append(
                    {
                        "cmd": cmd,
                        "wrapped_cmd": wrapped_cmd,
                        "attempt": attempt,
                        "returncode": returncode,
                        "timed_out": timed_out,
                        "stdout": stdout,
                        "stderr": stderr,
                        "stdout_parsed": _json_or_text(stdout),
                        "cwd": str(project_root),
                        "engine": effective_engine,
                        "worker_id": worker_id,
                        "worker_role": worker_role,
                        "started_at": started_at,
                        "ended_at": ended_at,
                        "failure_type": _classify_failure(returncode, stderr, timed_out) if returncode != 0 else "",
                    }
                )
                if returncode == 0:
                    succeeded = True
                    break
                if attempt < total_tries and backoff_sec > 0:
                    time.sleep(backoff_sec * attempt)

            if not succeeded:
                failure_type = _classify_failure(returncode, stderr, timed_out)
                if failure_type == "infrastructure":
                    # Try remaining engines in fallback chain before giving up
                    current_idx = fallback_chain.index(effective_engine) if effective_engine in fallback_chain else len(fallback_chain)
                    for next_engine in fallback_chain[current_idx + 1:]:
                        if next_engine == "shell":
                            # Shell fallback: run command directly
                            fb_started = _utc_now()
                            fb_timed_out = False
                            fb_rc = 1
                            fb_stdout = ""
                            fb_stderr = ""
                            try:
                                fb_proc = subprocess.run(cmd, shell=True, cwd=str(project_root), capture_output=True, text=True, timeout=timeout_sec)
                                fb_rc = int(fb_proc.returncode)
                                fb_stdout = _clip(fb_proc.stdout)
                                fb_stderr = _clip(fb_proc.stderr)
                            except subprocess.TimeoutExpired:
                                fb_timed_out = True
                                fb_rc = 124
                                fb_stderr = f"timeout>{timeout_sec}s"
                            command_results.append({
                                "cmd": cmd, "wrapped_cmd": cmd, "attempt": "infra-fallback-shell",
                                "returncode": fb_rc, "timed_out": fb_timed_out,
                                "stdout": fb_stdout, "stderr": fb_stderr,
                                "stdout_parsed": _json_or_text(fb_stdout), "cwd": str(project_root),
                                "engine": "shell", "worker_id": worker_id, "worker_role": worker_role,
                                "started_at": fb_started, "ended_at": _utc_now(),
                                "failure_type": _classify_failure(fb_rc, fb_stderr, fb_timed_out) if fb_rc != 0 else "",
                                "evidence_tag": f"infra-fallback:{effective_engine}\u2192shell",
                            })
                            if fb_rc == 0:
                                prev_engine = effective_engine
                                succeeded = True
                                fallback_evidence.append(f"infra-fallback:{prev_engine}\u2192shell")
                                effective_engine = "shell"
                                # Continue subsequent commands via direct shell execution.
                                template = "{cmd}"
                            break
                        fb_avail, _, _ = self._check_available(next_engine, project_root)
                        if not fb_avail:
                            continue
                        fb_template = self._default_template(next_engine)
                        try:
                            fb_wrapped = _render_worker_template(fb_template, lane, cmd, ctx)
                        except Exception:
                            continue
                        fb_started = _utc_now()
                        fb_timed_out = False
                        fb_rc = 1
                        fb_stdout = ""
                        fb_stderr = ""
                        try:
                            fb_proc = subprocess.run(fb_wrapped, shell=True, cwd=str(project_root), capture_output=True, text=True, timeout=timeout_sec)
                            fb_rc = int(fb_proc.returncode)
                            fb_stdout = _clip(fb_proc.stdout)
                            fb_stderr = _clip(fb_proc.stderr)
                        except subprocess.TimeoutExpired:
                            fb_timed_out = True
                            fb_rc = 124
                            fb_stderr = f"timeout>{timeout_sec}s"
                        command_results.append({
                            "cmd": cmd, "wrapped_cmd": fb_wrapped, "attempt": f"infra-fallback-{next_engine}",
                            "returncode": fb_rc, "timed_out": fb_timed_out,
                            "stdout": fb_stdout, "stderr": fb_stderr,
                            "stdout_parsed": _json_or_text(fb_stdout), "cwd": str(project_root),
                            "engine": next_engine, "worker_id": worker_id, "worker_role": worker_role,
                            "started_at": fb_started, "ended_at": _utc_now(),
                            "failure_type": _classify_failure(fb_rc, fb_stderr, fb_timed_out) if fb_rc != 0 else "",
                            "evidence_tag": f"infra-fallback:{effective_engine}\u2192{next_engine}",
                        })
                        if fb_rc == 0:
                            prev_engine = effective_engine
                            succeeded = True
                            fallback_evidence.append(f"infra-fallback:{prev_engine}\u2192{next_engine}")
                            effective_engine = next_engine
                            # Continue subsequent commands with the fallback engine wrapper.
                            template = fb_template
                            break

                    if not succeeded:
                        artifact = self._normalize_ai_result(
                            ctx=ctx,
                            lane=lane,
                            engine=effective_engine,
                            worker_id=worker_id,
                            worker_role=worker_role,
                            status="fail",
                            reason="command-failed",
                            attempts=command_results,
                        )
                        return LaneResult(
                            lane_id=lane_id,
                            status="fail",
                            evidence=[
                                "command-failed",
                                "ai-worker-execution",
                                f"engine:{effective_engine}",
                                f"artifact-json:{artifact}",
                            ] + fallback_evidence,
                            commands=command_results,
                            error=f"ai-worker command failed: {wrapped_cmd}",
                        )
                # Logic failure: attempt repair via the AI engine
                # (skip if infra fallback already resolved the failure)
                while not succeeded and repair_attempts < max_replan:
                    repair_attempts += 1
                    repair_prompt = _build_repair_prompt(cmd, stderr, stdout, repair_attempts, max_replan)
                    try:
                        repair_wrapped = _render_worker_template(template, lane, repair_prompt, ctx)
                    except Exception:
                        break
                    repair_started_at = _utc_now()
                    repair_timed_out = False
                    repair_returncode = 1
                    repair_stdout = ""
                    repair_stderr = ""
                    try:
                        repair_proc = subprocess.run(
                            repair_wrapped,
                            shell=True,
                            cwd=str(project_root),
                            capture_output=True,
                            text=True,
                            timeout=timeout_sec,
                        )
                        repair_returncode = int(repair_proc.returncode)
                        repair_stdout = _clip(repair_proc.stdout)
                        repair_stderr = _clip(repair_proc.stderr)
                    except subprocess.TimeoutExpired:
                        repair_timed_out = True
                        repair_returncode = 124
                        repair_stderr = f"timeout>{timeout_sec}s"
                    repair_ended_at = _utc_now()
                    command_results.append(
                        {
                            "cmd": repair_prompt,
                            "wrapped_cmd": repair_wrapped,
                            "attempt": f"repair-{repair_attempts}",
                            "returncode": repair_returncode,
                            "timed_out": repair_timed_out,
                            "stdout": repair_stdout,
                            "stderr": repair_stderr,
                            "stdout_parsed": _json_or_text(repair_stdout),
                            "cwd": str(project_root),
                            "engine": effective_engine,
                            "worker_id": worker_id,
                            "worker_role": worker_role,
                            "started_at": repair_started_at,
                            "ended_at": repair_ended_at,
                            "failure_type": _classify_failure(repair_returncode, repair_stderr, repair_timed_out) if repair_returncode != 0 else "",
                            "evidence_tag": f"logic-failure-repair-attempt:{repair_attempts}",
                        }
                    )
                    if repair_returncode == 0:
                        succeeded = True
                        break
                    # If repair itself hit infrastructure failure, stop repair loop
                    if _classify_failure(repair_returncode, repair_stderr, repair_timed_out) == "infrastructure":
                        break
                    # Update for next iteration context
                    stdout = repair_stdout
                    stderr = repair_stderr
                    timed_out = repair_timed_out
                    returncode = repair_returncode

            if not succeeded:
                repair_tag = "logic-failure-exhausted" if repair_attempts > 0 else "command-failed"
                artifact = self._normalize_ai_result(
                    ctx=ctx,
                    lane=lane,
                    engine=effective_engine,
                    worker_id=worker_id,
                    worker_role=worker_role,
                    status="fail",
                    reason="command-failed",
                    attempts=command_results,
                )
                return LaneResult(
                    lane_id=lane_id,
                    status="fail",
                    evidence=[
                        repair_tag,
                        "ai-worker-execution",
                        f"engine:{effective_engine}",
                        f"artifact-json:{artifact}",
                    ] + fallback_evidence,
                    commands=command_results,
                    error=f"ai-worker command failed: {wrapped_cmd}",
                )

        artifact = self._normalize_ai_result(
            ctx=ctx,
            lane=lane,
            engine=effective_engine,
            worker_id=worker_id,
            worker_role=worker_role,
            status="pass",
            reason="commands-pass",
            attempts=command_results,
        )
        evidence = ["commands-pass", "ai-worker-execution", f"engine:{effective_engine}"]
        for token in guardrail_audit[:5]:
            if token not in evidence:
                evidence.append(token)
        evidence.append(f"artifact-json:{artifact}")
        if worker_id:
            evidence.append(f"worker-id:{worker_id}")
        if worker_role:
            evidence.append(f"worker-role:{worker_role}")
        for fe in fallback_evidence:
            if fe not in evidence:
                evidence.append(fe)
        blocked_by_code_intel, code_intel_reason = _apply_code_intelligence(
            lane=lane,
            project_root=project_root,
            baseline_changed=baseline_changed,
            evidence=evidence,
            command_results=command_results,
        )
        if blocked_by_code_intel:
            return LaneResult(
                lane_id=lane_id,
                status="fail",
                evidence=evidence + ["code-intel-blocked", "ai-worker-execution", f"artifact-json:{artifact}"],
                commands=command_results,
                error=f"code intelligence blocked lane: {code_intel_reason}",
            )
        return LaneResult(
            lane_id=lane_id,
            status="pass",
            evidence=evidence,
            commands=command_results,
        )


class DelegateWorkerAdapter(BaseAdapter):
    name = "delegate-worker"

    def run_lane(self, lane: Dict[str, Any], project_root: Path) -> LaneResult:
        lane_id = str(lane.get("id", "unknown-lane"))
        commands = [str(c).strip() for c in lane.get("commands", []) if str(c).strip()]
        if not commands:
            return LaneResult(
                lane_id=lane_id,
                status="pass",
                evidence=["contract-only-lane", "delegate-worker-execution"],
                commands=[],
            )

        runtime = lane.get("_runtime", {})
        ctx = _runtime_ctx(lane, project_root)
        worker_id = str(runtime.get("worker_id", "")).strip()
        worker_role = str(runtime.get("worker_role", "")).strip()

        timeout_sec = int(runtime.get("delegate_timeout_sec", lane.get("delegate_timeout_sec", 180)))
        poll_sec = float(runtime.get("delegate_poll_sec", lane.get("delegate_poll_sec", 0.3)))
        if timeout_sec < 5:
            timeout_sec = 5
        if poll_sec < 0.05:
            poll_sec = 0.05

        guardrail_audit: List[str] = []
        for cmd in commands:
            guard = _guardrail_check(lane, cmd, project_root, phase="lane")
            if bool(guard.get("audit_only", False)):
                guardrail_audit.append(f"guardrail-audit:{guard.get('reason', '')}")
            if not bool(guard.get("allowed", True)):
                return LaneResult(
                    lane_id=lane_id,
                    status="fail",
                    evidence=[
                        "guardrail-blocked",
                        "delegate-worker-execution",
                        f"guardrail-reason:{guard.get('reason', '')}",
                    ],
                    commands=[
                        {
                            "cmd": cmd,
                            "returncode": 126,
                            "stderr": f"blocked by command guardrail: {guard.get('reason', '')}",
                            "guardrail": guard,
                        }
                    ],
                    error=f"delegate command blocked by guardrail: {cmd}",
                )

        request = create_request(
            project_root=project_root,
            run_id=str(ctx.get("run_id", "")),
            lane_id=lane_id,
            owner_role=str(lane.get("owner_role", "unassigned")),
            commands=commands,
            runtime={
                "target_worker_id": worker_id,
                "worker_role": worker_role,
                "worker_engine": str(runtime.get("worker_engine", lane.get("worker_engine", ""))),
                "worker_command_template": str(runtime.get("worker_command_template", lane.get("worker_command_template", ""))),
                "delegate_timeout_sec": timeout_sec,
            },
        )
        request_id = str(request.get("request_id", ""))
        response = wait_for_response(project_root, request_id=request_id, timeout_sec=timeout_sec, poll_sec=poll_sec)
        if not response:
            return LaneResult(
                lane_id=lane_id,
                status="fail",
                evidence=["delegate-worker-timeout", f"request-id:{request_id}"],
                commands=[{"request_id": request_id, "returncode": 124, "stderr": f"wait timeout>{timeout_sec}s"}],
                error=f"delegate worker response timeout: {request_id}",
            )

        status = str(response.get("status", "fail")).strip().lower()
        results = response.get("results", [])
        commands_out = results if isinstance(results, list) else []
        evidence = [
            "delegate-worker-execution",
            f"request-id:{request_id}",
            f"response-status:{status}",
        ]
        if worker_id:
            evidence.append(f"worker-id:{worker_id}")
        if worker_role:
            evidence.append(f"worker-role:{worker_role}")
        for token in guardrail_audit[:5]:
            if token not in evidence:
                evidence.append(token)

        if status == "pass":
            evidence.append("commands-pass")
            return LaneResult(
                lane_id=lane_id,
                status="pass",
                evidence=evidence,
                commands=commands_out,
            )
        return LaneResult(
            lane_id=lane_id,
            status="fail",
            evidence=evidence + ["command-failed"],
            commands=commands_out,
            error=str(response.get("error", "")).strip() or f"delegate worker failed: {request_id}",
        )


def resolve_adapter(name: str) -> Tuple[BaseAdapter, List[str]]:
    notes: List[str] = []
    requested = name.strip().lower()
    if requested == "auto":
        if shutil.which("tmux"):
            return TmuxWorkerAdapter(), notes
        if shutil.which("git"):
            return WorktreeWorkerAdapter(), notes
        return InlineWorkerAdapter(), notes

    if requested == "tmux-worker":
        if shutil.which("tmux"):
            return TmuxWorkerAdapter(), notes
        notes.append("tmux unavailable, fallback to inline-worker.")
        return InlineWorkerAdapter(), notes

    if requested == "worktree-worker":
        if shutil.which("git"):
            return WorktreeWorkerAdapter(), notes
        notes.append("git unavailable, fallback to inline-worker.")
        return InlineWorkerAdapter(), notes

    if requested == "process-worker":
        return ProcessWorkerAdapter(), notes

    if requested == "delegate-worker":
        return DelegateWorkerAdapter(), notes

    if requested == "ai-worker":
        return AiCliWorkerAdapter(), notes

    if requested in {"inline", "inline-worker"}:
        return InlineWorkerAdapter(), notes

    notes.append(f"unknown adapter '{name}', fallback to inline-worker.")
    return InlineWorkerAdapter(), notes
