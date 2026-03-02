#!/usr/bin/env python3
"""Delegate worker processor for external lane execution."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List

from .delegate_bus import claim_next_request, complete_request, queue_stats


def _clip(text: str, n: int = 4000) -> str:
    return text[-n:] if text else ""


def _run(cmd: str, cwd: Path, timeout_sec: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        shell=True,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=max(5, timeout_sec),
    )


def _engine_available(engine: str, project_root: Path) -> tuple[bool, str]:
    engine = engine.strip().lower()
    if engine in {"", "shell", "process"}:
        return True, ""
    if engine == "codex":
        if not shutil.which("codex"):
            return False, "codex-cli-not-found"
        probe = subprocess.run("codex login status", shell=True, cwd=str(project_root), capture_output=True, text=True)
        if probe.returncode != 0:
            return False, "codex-not-logged-in"
        return True, ""
    if engine == "gemini":
        if not shutil.which("gemini"):
            return False, "gemini-cli-not-found"
        if not os.environ.get("GEMINI_API_KEY", "").strip():
            return False, "gemini-api-key-missing"
        return True, ""
    return False, f"unsupported-engine:{engine}"


def _wrap_command(engine: str, cmd: str, template: str) -> str:
    engine = engine.strip().lower()
    if template.strip():
        base = template.strip()
        if "{cmd}" not in base:
            base = base + " {cmd}"
        return base.format(cmd=cmd)
    if engine == "codex":
        return f'codex exec --skip-git-repo-check "{cmd}"'
    if engine == "gemini":
        return f'gemini -p "{cmd}" --yolo'
    return cmd


def process_one_request(
    project_root: Path,
    worker_id: str,
    role_filter: str = "",
    engine: str = "shell",
    command_template: str = "",
    timeout_sec: int = 180,
) -> Dict[str, Any]:
    path, req = claim_next_request(project_root, role_filter=role_filter, worker_id=worker_id)
    if not path or not req:
        return {"processed": 0, "status": "idle"}

    runtime = req.get("runtime", {}) if isinstance(req.get("runtime", {}), dict) else {}
    engine_eff = str(runtime.get("worker_engine", "")).strip().lower() or engine.strip().lower()
    template_eff = str(runtime.get("worker_command_template", "")).strip() or command_template.strip()
    timeout_eff = int(runtime.get("delegate_timeout_sec", timeout_sec))
    if timeout_eff < 10:
        timeout_eff = 10

    ok_engine, reason = _engine_available(engine_eff, project_root)
    results: List[Dict[str, Any]] = []
    if not ok_engine:
        response = complete_request(
            project_root=project_root,
            processing_path=path,
            request_row=req,
            status="fail",
            results=[
                {
                    "cmd": "",
                    "wrapped_cmd": "",
                    "returncode": 1,
                    "stdout": "",
                    "stderr": reason,
                }
            ],
            worker_meta={"worker_id": worker_id, "engine": engine_eff, "role_filter": role_filter},
            error=reason,
        )
        return {"processed": 1, "status": "fail", "response": response}

    status = "pass"
    error = ""
    commands = [str(c).strip() for c in req.get("commands", []) if str(c).strip()]
    for cmd in commands:
        wrapped = _wrap_command(engine_eff, cmd, template_eff)
        try:
            proc = _run(wrapped, project_root, timeout_eff)
            row = {
                "cmd": cmd,
                "wrapped_cmd": wrapped,
                "returncode": proc.returncode,
                "stdout": _clip(proc.stdout),
                "stderr": _clip(proc.stderr),
            }
        except subprocess.TimeoutExpired:
            row = {
                "cmd": cmd,
                "wrapped_cmd": wrapped,
                "returncode": 124,
                "stdout": "",
                "stderr": f"timeout>{timeout_eff}s",
            }
        results.append(row)
        if int(row.get("returncode", 1)) != 0:
            status = "fail"
            error = f"delegate command failed: {cmd}"
            break

    response = complete_request(
        project_root=project_root,
        processing_path=path,
        request_row=req,
        status=status,
        results=results,
        worker_meta={"worker_id": worker_id, "engine": engine_eff, "role_filter": role_filter},
        error=error,
    )
    return {"processed": 1, "status": status, "response": response}


def serve(
    project_root: Path,
    worker_id: str,
    role_filter: str = "",
    engine: str = "shell",
    command_template: str = "",
    timeout_sec: int = 180,
    interval_sec: float = 0.5,
    max_jobs: int = 0,
    idle_exit_sec: float = 0.0,
) -> Dict[str, Any]:
    processed = 0
    passed = 0
    failed = 0
    idle_since = time.time()
    while True:
        out = process_one_request(
            project_root=project_root,
            worker_id=worker_id,
            role_filter=role_filter,
            engine=engine,
            command_template=command_template,
            timeout_sec=timeout_sec,
        )
        if out.get("processed", 0) > 0:
            processed += 1
            idle_since = time.time()
            if out.get("status") == "pass":
                passed += 1
            elif out.get("status") == "fail":
                failed += 1
        else:
            if idle_exit_sec > 0 and (time.time() - idle_since) >= idle_exit_sec:
                break
            time.sleep(max(0.05, interval_sec))

        if max_jobs > 0 and processed >= max_jobs:
            break

    stats = queue_stats(project_root)
    return {
        "processed": processed,
        "passed": passed,
        "failed": failed,
        "queue": stats,
    }

