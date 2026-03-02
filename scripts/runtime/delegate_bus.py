#!/usr/bin/env python3
"""File-backed delegate worker request/response bus."""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def delegates_root(project_root: Path) -> Path:
    return project_root.resolve() / ".plan-executor" / "delegates"


def _dirs(project_root: Path) -> Dict[str, Path]:
    root = delegates_root(project_root)
    d = {
        "pending": root / "requests" / "pending",
        "processing": root / "requests" / "processing",
        "done": root / "requests" / "done",
        "responses": root / "responses",
    }
    for path in d.values():
        path.mkdir(parents=True, exist_ok=True)
    return d


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def create_request(
    project_root: Path,
    run_id: str,
    lane_id: str,
    owner_role: str,
    commands: List[str],
    runtime: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    d = _dirs(project_root)
    request_id = f"req-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    payload = {
        "request_id": request_id,
        "run_id": run_id,
        "lane_id": lane_id,
        "owner_role": owner_role,
        "commands": [str(c) for c in commands if str(c).strip()],
        "runtime": dict(runtime or {}),
        "created_at": utc_now(),
        "status": "pending",
    }
    path = d["pending"] / f"{request_id}.json"
    _write_json(path, payload)
    return payload


def list_requests(project_root: Path, bucket: str = "pending") -> List[Path]:
    d = _dirs(project_root)
    if bucket not in d:
        return []
    return sorted(d[bucket].glob("*.json"))


def claim_next_request(
    project_root: Path,
    role_filter: str = "",
    worker_id: str = "",
) -> Tuple[Path | None, Dict[str, Any] | None]:
    d = _dirs(project_root)
    role_filter = role_filter.strip().lower()
    worker_id = worker_id.strip()
    for path in list_requests(project_root, "pending"):
        try:
            row = _read_json(path)
        except Exception:
            continue
        owner_role = str(row.get("owner_role", "")).strip().lower()
        if role_filter and owner_role and owner_role != role_filter:
            continue
        runtime = row.get("runtime", {}) if isinstance(row.get("runtime", {}), dict) else {}
        target_worker = str(runtime.get("target_worker_id", "")).strip()
        if worker_id and target_worker and target_worker != worker_id:
            continue
        dst = d["processing"] / path.name
        try:
            path.replace(dst)
        except OSError:
            continue
        row["status"] = "processing"
        row["claimed_at"] = utc_now()
        _write_json(dst, row)
        return dst, row
    return None, None


def complete_request(
    project_root: Path,
    processing_path: Path,
    request_row: Dict[str, Any],
    status: str,
    results: List[Dict[str, Any]],
    worker_meta: Dict[str, Any],
    error: str = "",
) -> Dict[str, Any]:
    d = _dirs(project_root)
    row = dict(request_row)
    row["status"] = status
    row["completed_at"] = utc_now()
    row["results"] = results
    row["worker"] = worker_meta
    row["error"] = error

    request_id = str(row.get("request_id", processing_path.stem)).strip()
    response = {
        "request_id": request_id,
        "run_id": str(row.get("run_id", "")),
        "lane_id": str(row.get("lane_id", "")),
        "status": status,
        "results": results,
        "worker": worker_meta,
        "error": error,
        "completed_at": row["completed_at"],
    }
    _write_json(d["responses"] / f"{request_id}.json", response)
    _write_json(d["done"] / processing_path.name, row)
    processing_path.unlink(missing_ok=True)
    return response


def wait_for_response(
    project_root: Path,
    request_id: str,
    timeout_sec: float = 120.0,
    poll_sec: float = 0.3,
) -> Dict[str, Any] | None:
    d = _dirs(project_root)
    path = d["responses"] / f"{request_id}.json"
    deadline = time.time() + max(1.0, timeout_sec)
    while time.time() < deadline:
        if path.exists():
            try:
                return _read_json(path)
            except Exception:
                pass
        time.sleep(max(0.05, poll_sec))
    return None


def queue_stats(project_root: Path) -> Dict[str, int]:
    d = _dirs(project_root)
    return {
        "pending": len(list(d["pending"].glob("*.json"))),
        "processing": len(list(d["processing"].glob("*.json"))),
        "done": len(list(d["done"].glob("*.json"))),
        "responses": len(list(d["responses"].glob("*.json"))),
    }

