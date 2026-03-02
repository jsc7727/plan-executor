#!/usr/bin/env python3
"""Specialist agent registry for plan-executor runtime."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List


@dataclass
class Specialist:
    id: str
    role: str
    description: str
    capabilities: List[str]
    tools: List[str]
    aliases: List[str]


DEFAULT_SPECIALISTS: List[Specialist] = [
    Specialist("orchestrator", "Orchestrator", "Global plan owner and scheduler", ["planning", "scheduling"], ["runtime"], ["coordinator"]),
    Specialist("integrator", "Integrator", "Merge checkpoints and final decisions", ["merge", "verification"], ["runtime", "review"], ["merge-manager"]),
    Specialist("planner", "Planner", "Scope and requirement prioritization", ["planning", "prioritization"], ["docs"], ["product-manager", "pm"]),
    Specialist("architect", "Architect", "System design and interface boundaries", ["architecture", "api-design"], ["docs", "code"], []),
    Specialist("security-reviewer", "Security Reviewer", "Threat modeling and security checks", ["security-audit", "secret-scan"], ["scan", "review"], ["security"]),
    Specialist("designer", "Designer", "UX flow and UI artifact design", ["ux-flow", "ui-spec"], ["docs", "assets"], ["art-designer", "ui-designer"]),
    Specialist("frontend", "Frontend Engineer", "UI implementation and FE testing", ["frontend-dev", "accessibility"], ["code", "test"], ["frontend-engineer", "fe"]),
    Specialist("backend", "Backend Engineer", "API/data/backend implementation", ["backend-dev", "data-modeling"], ["code", "test"], ["backend-engineer", "be"]),
    Specialist("qa", "QA Engineer", "Test planning and regression checks", ["test-plan", "regression"], ["test", "report"], ["qa-engineer", "tester", "reviewer"]),
    Specialist("devops-engineer", "DevOps Engineer", "Build/deploy/runtime reliability", ["ci-cd", "ops"], ["deploy", "monitor"], ["devops"]),
    Specialist("data-engineer", "Data Engineer", "Pipelines and data quality", ["etl", "dq-checks"], ["code", "query"], ["data"]),
    Specialist("performance-engineer", "Performance Engineer", "Profiling and optimization", ["profiling", "benchmark"], ["profile", "test"], ["perf"]),
    Specialist("reliability-engineer", "Reliability Engineer", "Incident prevention and recovery", ["sre", "runbook"], ["monitor", "ops"], ["sre"]),
    Specialist("documentation-writer", "Documentation Writer", "Technical writing and handoff docs", ["docs", "release-notes"], ["docs"], ["writer"]),
]


ROLE_ALIASES: Dict[str, str] = {
    "product-manager": "planner",
    "project-manager": "planner",
    "frontend-engineer": "frontend",
    "backend-engineer": "backend",
    "qa-engineer": "qa",
    "programmer": "frontend",
    "tester": "qa",
    "reviewer": "qa",
}


def registry_path(project_root: Path) -> Path:
    return project_root.resolve() / ".plan-executor" / "agents" / "registry.json"


def load_registry(project_root: Path) -> List[Specialist]:
    path = registry_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        save_registry(project_root, DEFAULT_SPECIALISTS)
        return list(DEFAULT_SPECIALISTS)
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    out: List[Specialist] = []
    for item in payload.get("specialists", []):
        out.append(
            Specialist(
                id=str(item.get("id", "")).strip(),
                role=str(item.get("role", "")).strip(),
                description=str(item.get("description", "")).strip(),
                capabilities=[str(x) for x in item.get("capabilities", [])],
                tools=[str(x) for x in item.get("tools", [])],
                aliases=[str(x) for x in item.get("aliases", [])],
            )
        )
    return out


def save_registry(project_root: Path, specialists: List[Specialist]) -> None:
    path = registry_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, object] = {
        "specialists": [
            {
                "id": s.id,
                "role": s.role,
                "description": s.description,
                "capabilities": s.capabilities,
                "tools": s.tools,
                "aliases": s.aliases,
            }
            for s in specialists
        ]
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def index_registry(project_root: Path) -> Dict[str, Specialist]:
    index: Dict[str, Specialist] = {}
    for s in load_registry(project_root):
        keys = [s.id, *s.aliases]
        for key in keys:
            normalized = str(key).strip().lower()
            if normalized:
                index[normalized] = s
    return index


def resolve_specialist(project_root: Path, role_or_id: str) -> Specialist | None:
    target = str(role_or_id).strip().lower()
    if not target:
        return None
    index = index_registry(project_root)
    if target in index:
        return index[target]
    alias_target = ROLE_ALIASES.get(target, "")
    if alias_target and alias_target in index:
        return index[alias_target]
    return None


def get_specialist(project_root: Path, specialist_id: str) -> Specialist | None:
    return resolve_specialist(project_root, specialist_id)
