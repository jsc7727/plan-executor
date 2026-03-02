#!/usr/bin/env python3
"""Persistent role worker for frontstage_codex_teams.

Protocol:
- stdin: one JSON line per request
- stdout: one JSON line response per request
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List


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


def compact_text(value: str, limit: int = 4000) -> str:
    text = " ".join((value or "").replace("\r", " ").replace("\n", " ").split())
    return text[-limit:] if len(text) > limit else text


def memory_from_payload(payload: Dict[str, Any], limit: int) -> List[str]:
    out: List[str] = []
    notes = payload.get("notes", [])
    if isinstance(notes, list):
        for row in notes:
            token = compact_text(str(row), 400)
            if token:
                out.append(f"note:{token}")

    for key in ("proposals", "stages"):
        rows = payload.get(key, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("proposal_id", "")).strip() or str(row.get("stage_id", "")).strip()
            title = str(row.get("name", "")).strip() or str(row.get("title", "")).strip()
            summary = compact_text(str(row.get("summary", "")).strip() or str(row.get("content", "")).strip(), 300)
            token = f"proposal:{pid}|{title}|{summary}".strip("|")
            if token:
                out.append(token)

    critiques = payload.get("critiques", [])
    if isinstance(critiques, list):
        for row in critiques:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("proposal_id", "")).strip()
            severity = str(row.get("severity", "medium")).strip().lower()
            content = compact_text(str(row.get("content", "")), 280)
            if pid and content:
                out.append(f"critique:{pid}|{severity}|{content}")
    if limit <= 0:
        return out
    return out[-limit:]


def build_prompt_with_memory(prompt: str, memory: List[str], max_lines: int) -> str:
    if max_lines <= 0 or not memory:
        return prompt
    lines = memory[-max_lines:]
    memory_block = "\n".join([f"- {x}" for x in lines])
    return f"{prompt}\n\n# role_memory\n{memory_block}\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Frontstage role worker")
    parser.add_argument("--role", required=True, help="Role label")
    parser.add_argument("--project-root", default=".", help="Project root")
    parser.add_argument("--agent-cmd-template", required=True, help="Agent command template")
    parser.add_argument("--timeout-sec", type=int, default=180, help="Per-call timeout")
    parser.add_argument("--memory-lines", type=int, default=30, help="Role memory capacity")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    role = str(args.role).strip().lower()
    project_root = Path(args.project_root).resolve()
    timeout_sec = max(5, int(args.timeout_sec))
    memory_cap = max(0, int(args.memory_lines))
    memory: List[str] = []

    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue

        request_id = ""
        try:
            req = json.loads(line)
            if not isinstance(req, dict):
                raise ValueError("request must be object")
        except Exception as exc:
            resp = {
                "request_id": request_id,
                "role": role,
                "phase": "",
                "round_index": 0,
                "returncode": 1,
                "stdout": "",
                "stderr": "",
                "payload": None,
                "error": f"invalid-request:{exc}",
                "memory_size": len(memory),
            }
            print(json.dumps(resp, ensure_ascii=True), flush=True)
            continue

        request_id = str(req.get("request_id", "")).strip()
        kind = str(req.get("kind", "run")).strip().lower()
        phase = str(req.get("phase", "")).strip().lower()
        round_index = int(req.get("round_index", 0))
        objective = str(req.get("objective", "")).strip()
        prompt = str(req.get("prompt", ""))

        if kind == "shutdown":
            resp = {
                "request_id": request_id,
                "role": role,
                "phase": phase,
                "round_index": round_index,
                "returncode": 0,
                "stdout": "",
                "stderr": "",
                "payload": {"ok": True, "message": "shutdown"},
                "error": "",
                "memory_size": len(memory),
            }
            print(json.dumps(resp, ensure_ascii=True), flush=True)
            break

        prompt_with_memory = build_prompt_with_memory(prompt, memory, max_lines=min(20, memory_cap))
        context = {
            "role": role,
            "phase": phase,
            "objective": objective,
            "round_index": str(round_index),
            "prompt": prompt_with_memory.replace('"', '\\"'),
        }
        try:
            cmd = str(args.agent_cmd_template).format(**context)
        except Exception as exc:
            resp = {
                "request_id": request_id,
                "role": role,
                "phase": phase,
                "round_index": round_index,
                "returncode": 1,
                "stdout": "",
                "stderr": "",
                "payload": None,
                "error": f"invalid-template:{exc}",
                "memory_size": len(memory),
            }
            print(json.dumps(resp, ensure_ascii=True), flush=True)
            continue

        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            stdout = (proc.stdout or "")[-8000:]
            stderr = (proc.stderr or "")[-4000:]
            payload = parse_json_from_text(stdout)
            if isinstance(payload, dict):
                updates = memory_from_payload(payload, limit=memory_cap)
                if updates:
                    memory.extend(updates)
                    if memory_cap > 0:
                        memory = memory[-memory_cap:]
            resp = {
                "request_id": request_id,
                "role": role,
                "phase": phase,
                "round_index": round_index,
                "returncode": int(proc.returncode),
                "stdout": stdout,
                "stderr": stderr,
                "payload": payload,
                "error": "",
                "memory_size": len(memory),
            }
            print(json.dumps(resp, ensure_ascii=True), flush=True)
            continue
        except subprocess.TimeoutExpired:
            resp = {
                "request_id": request_id,
                "role": role,
                "phase": phase,
                "round_index": round_index,
                "returncode": 124,
                "stdout": "",
                "stderr": f"timeout>{timeout_sec}s",
                "payload": None,
                "error": "timeout",
                "memory_size": len(memory),
            }
            print(json.dumps(resp, ensure_ascii=True), flush=True)
            continue

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

