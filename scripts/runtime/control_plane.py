#!/usr/bin/env python3
"""Control plane for IPC + file double logging."""

from __future__ import annotations

import json
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Any, Dict, List, Tuple


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def control_root(project_root: Path) -> Path:
    return project_root.resolve() / ".plan-executor" / "control"


def control_message_path(project_root: Path, run_id: str) -> Path:
    return control_root(project_root) / "messages" / f"{run_id}.jsonl"


def control_ipc_log_path(project_root: Path) -> Path:
    return control_root(project_root) / "ipc.log"


def _coerce_message(message: Dict[str, Any], source: str) -> Dict[str, Any]:
    out = dict(message)
    out["msg_id"] = str(out.get("msg_id", "")).strip() or f"msg-{uuid.uuid4().hex[:12]}"
    out["ts"] = str(out.get("ts", "")).strip() or utc_now()
    out["kind"] = str(out.get("kind", "note")).strip() or "note"
    out["run_id"] = str(out.get("run_id", "")).strip()
    out["source"] = str(out.get("source", source)).strip() or source
    payload = out.get("payload", {})
    out["payload"] = payload if isinstance(payload, dict) else {"value": payload}
    return out


def append_control_message(project_root: Path, message: Dict[str, Any], source: str = "file") -> Dict[str, Any]:
    row = _coerce_message(message, source=source)
    run_id = str(row.get("run_id", "")).strip()
    if not run_id:
        raise ValueError("control message requires run_id")

    msg_path = control_message_path(project_root, run_id)
    msg_path.parent.mkdir(parents=True, exist_ok=True)
    with msg_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=True) + "\n")

    if row.get("source") == "ipc":
        log_path = control_ipc_log_path(project_root)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")
    return row


def read_control_messages(project_root: Path, run_id: str, offset: int = 0, limit: int = 200) -> Tuple[List[Dict[str, Any]], int]:
    path = control_message_path(project_root, run_id)
    if not path.exists():
        return [], max(0, offset)

    lines = path.read_text(encoding="utf-8").splitlines()
    total = len(lines)
    start = max(0, min(offset, total))
    selected = lines[start:] if limit <= 0 else lines[start : start + max(1, limit)]
    out: List[Dict[str, Any]] = []
    for line in selected:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                out.append(row)
        except json.JSONDecodeError:
            continue
    new_offset = min(total, start + len(selected))
    return out, new_offset


def count_control_messages(project_root: Path, run_id: str) -> int:
    path = control_message_path(project_root, run_id)
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def send_control_ipc(host: str, port: int, message: Dict[str, Any], timeout_sec: float = 5.0) -> Dict[str, Any]:
    payload = json.dumps(_coerce_message(message, source="ipc"), ensure_ascii=True) + "\n"
    with socket.create_connection((host, int(port)), timeout=timeout_sec) as conn:
        conn.sendall(payload.encode("utf-8"))
        conn.shutdown(socket.SHUT_WR)
        chunks: List[bytes] = []
        while True:
            buf = conn.recv(4096)
            if not buf:
                break
            chunks.append(buf)
    raw = b"".join(chunks).decode("utf-8", errors="replace").strip()
    if not raw:
        return {"ok": True, "accepted": 1, "errors": 0}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {"ok": True, "accepted": 1, "errors": 0, "response_text": raw}


def serve_control_ipc(
    project_root: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    stop_event: Event | None = None,
    poll_sec: float = 0.25,
) -> None:
    stop_event = stop_event or Event()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, int(port)))
        server.listen(16)
        server.settimeout(max(0.05, poll_sec))

        while not stop_event.is_set():
            try:
                conn, addr = server.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with conn:
                chunks: List[bytes] = []
                while True:
                    buf = conn.recv(4096)
                    if not buf:
                        break
                    chunks.append(buf)
                raw = b"".join(chunks).decode("utf-8", errors="replace")
                accepted = 0
                errors = 0
                for line in raw.splitlines():
                    if not line.strip():
                        continue
                    try:
                        message = json.loads(line)
                    except json.JSONDecodeError:
                        errors += 1
                        continue
                    if not isinstance(message, dict):
                        errors += 1
                        continue
                    message.setdefault("source", "ipc")
                    message.setdefault("payload", {})
                    message["payload"]["remote_addr"] = f"{addr[0]}:{addr[1]}"
                    try:
                        append_control_message(project_root, message, source="ipc")
                        accepted += 1
                    except Exception:
                        errors += 1
                resp = {
                    "ok": errors == 0,
                    "accepted": accepted,
                    "errors": errors,
                    "ts": utc_now(),
                }
                try:
                    conn.sendall((json.dumps(resp, ensure_ascii=True) + "\n").encode("utf-8"))
                except OSError:
                    pass

