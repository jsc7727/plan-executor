#!/usr/bin/env python3
"""File-backed message bus for inter-agent communication."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def bus_path(project_root: Path, run_id: str) -> Path:
    return project_root.resolve() / ".plan-executor" / "messages" / f"{run_id}.jsonl"


@dataclass
class Message:
    ts: str
    run_id: str
    from_agent: str
    to_agent: str
    kind: str
    content: str
    metadata: Dict[str, str]


def send_message(
    project_root: Path,
    run_id: str,
    from_agent: str,
    to_agent: str,
    kind: str,
    content: str,
    metadata: Dict[str, str] | None = None,
) -> Message:
    path = bus_path(project_root, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    msg = Message(
        ts=utc_now(),
        run_id=run_id,
        from_agent=from_agent,
        to_agent=to_agent,
        kind=kind,
        content=content,
        metadata=metadata or {},
    )
    with path.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "ts": msg.ts,
                    "run_id": msg.run_id,
                    "from_agent": msg.from_agent,
                    "to_agent": msg.to_agent,
                    "kind": msg.kind,
                    "content": msg.content,
                    "metadata": msg.metadata,
                },
                ensure_ascii=True,
            )
            + "\n"
        )
    return msg


def list_messages(
    project_root: Path,
    run_id: str,
    to_agent: str = "",
    from_agent: str = "",
    kind: str = "",
    limit: int = 100,
) -> List[Message]:
    path = bus_path(project_root, run_id)
    if not path.exists():
        return []

    to_agent = to_agent.strip().lower()
    from_agent = from_agent.strip().lower()
    kind = kind.strip().lower()

    out: List[Message] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = Message(
            ts=str(row.get("ts", "")),
            run_id=str(row.get("run_id", run_id)),
            from_agent=str(row.get("from_agent", "")),
            to_agent=str(row.get("to_agent", "")),
            kind=str(row.get("kind", "")),
            content=str(row.get("content", "")),
            metadata={str(k): str(v) for k, v in row.get("metadata", {}).items()},
        )
        if to_agent and msg.to_agent.lower() != to_agent:
            continue
        if from_agent and msg.from_agent.lower() != from_agent:
            continue
        if kind and msg.kind.lower() != kind:
            continue
        out.append(msg)

    if limit > 0:
        out = out[-limit:]
    return out

