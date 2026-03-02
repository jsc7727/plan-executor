#!/usr/bin/env python3
"""Event/state persistence for plan-executor runtime."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class EventStore:
    """Persist runtime events and state under <project-root>/.plan-executor/."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.base = self.project_root / ".plan-executor"
        self.events_dir = self.base / "events"
        self.state_dir = self.base / "state"
        self.logs_dir = self.base / "logs"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def event_path(self, run_id: str) -> Path:
        return self.events_dir / f"{run_id}.jsonl"

    def state_path(self, run_id: str) -> Path:
        return self.state_dir / f"{run_id}.json"

    def append_event(self, run_id: str, event: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        record = {
            "ts": utc_now(),
            "run_id": run_id,
            "event": event,
            "payload": payload,
        }
        with self.event_path(run_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=True) + "\n")
        return record

    def read_events(self, run_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        path = self.event_path(run_id)
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        selected = lines[-limit:] if limit > 0 else lines
        out: List[Dict[str, Any]] = []
        for line in selected:
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                # Keep runtime resilient to partial writes.
                continue
        return out

    def write_state(self, run_id: str, state: Dict[str, Any]) -> None:
        state = dict(state)
        state["updated_at"] = utc_now()
        self.state_path(run_id).write_text(json.dumps(state, indent=2, ensure_ascii=True), encoding="utf-8")

    def read_state(self, run_id: str) -> Dict[str, Any] | None:
        path = self.state_path(run_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_runs(self) -> List[str]:
        runs: List[str] = []
        for p in sorted(self.state_dir.glob("*.json")):
            runs.append(p.stem)
        return runs

