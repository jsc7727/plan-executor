#!/usr/bin/env python3
"""Heuristic plan-search utilities for replan candidate selection."""

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple


PLAN_KEYS = {
    "replace_pending_lanes",
    "update_lanes",
    "append_lanes",
    "dag",
    "checkpoints",
    "set_limits",
    "reason",
}


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _to_lanes(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: List[Dict[str, Any]] = []
    for row in value:
        if isinstance(row, dict):
            out.append(dict(row))
    return out


def extract_candidate_patch(candidate: Dict[str, Any]) -> Dict[str, Any]:
    patch = candidate.get("plan_patch")
    if isinstance(patch, dict):
        return dict(patch)
    out: Dict[str, Any] = {}
    for key in PLAN_KEYS:
        if key in candidate:
            out[key] = candidate[key]
    return out


def _lane_ids_from_patch(patch: Dict[str, Any], baseline_lane_ids: List[str]) -> List[str]:
    lane_ids = [str(x).strip() for x in baseline_lane_ids if str(x).strip()]
    replace_pending = _to_lanes(patch.get("replace_pending_lanes", []))
    append_lanes = _to_lanes(patch.get("append_lanes", []))
    update_lanes = _to_lanes(patch.get("update_lanes", []))

    if replace_pending:
        keep = [x for x in lane_ids if x]
        # Keep completed/running lanes are unknown at this layer; retain baseline order.
        # Replaced pending lanes are appended in candidate order.
        repl_ids = [str(l.get("id", "")).strip() for l in replace_pending if str(l.get("id", "")).strip()]
        lane_ids = keep + [x for x in repl_ids if x not in keep]

    for lane in append_lanes:
        lane_id = str(lane.get("id", "")).strip()
        if lane_id and lane_id not in lane_ids:
            lane_ids.append(lane_id)
    for lane in update_lanes:
        lane_id = str(lane.get("id", "")).strip()
        if lane_id and lane_id not in lane_ids:
            lane_ids.append(lane_id)
    return lane_ids


def _commands_coverage(patch: Dict[str, Any]) -> float:
    lanes = _to_lanes(patch.get("replace_pending_lanes", [])) + _to_lanes(patch.get("append_lanes", [])) + _to_lanes(patch.get("update_lanes", []))
    if not lanes:
        return 0.0
    with_commands = 0
    for lane in lanes:
        cmds = lane.get("commands", [])
        if isinstance(cmds, list) and any(str(x).strip() for x in cmds):
            with_commands += 1
    return with_commands / max(1, len(lanes))


def _checkpoint_coverage(checkpoints: Any, lane_ids: List[str]) -> float:
    if not isinstance(checkpoints, list) or not lane_ids:
        return 0.0
    covered: Set[str] = set()
    for cp in checkpoints:
        if not isinstance(cp, dict):
            continue
        for lane_id in cp.get("after_lanes", []):
            token = str(lane_id).strip()
            if token:
                covered.add(token)
    total = len([x for x in lane_ids if x])
    if total <= 0:
        return 0.0
    hit = len([x for x in lane_ids if x in covered])
    return hit / total


def _has_cycle(graph: Dict[str, Set[str]]) -> bool:
    color: Dict[str, int] = {}  # 0=white,1=gray,2=black

    def dfs(node: str) -> bool:
        state = color.get(node, 0)
        if state == 1:
            return True
        if state == 2:
            return False
        color[node] = 1
        for nxt in graph.get(node, set()):
            if dfs(nxt):
                return True
        color[node] = 2
        return False

    for node in list(graph.keys()):
        if color.get(node, 0) == 0 and dfs(node):
            return True
    return False


def _dag_risk(dag: Any, lane_ids: List[str]) -> Tuple[int, int, bool]:
    if not isinstance(dag, dict):
        return 0, 0, False
    nodes = dag.get("nodes", [])
    if not isinstance(nodes, list):
        return 0, 0, False

    valid_ids = {str(x).strip() for x in lane_ids if str(x).strip()}
    graph: Dict[str, Set[str]] = {}
    unresolved = 0
    self_dep = 0
    for row in nodes:
        if not isinstance(row, dict):
            continue
        node_id = str(row.get("id", "")).strip()
        if not node_id:
            continue
        deps = {str(x).strip() for x in row.get("depends_on", []) if str(x).strip()}
        graph.setdefault(node_id, set())
        for dep in deps:
            if dep == node_id:
                self_dep += 1
            if dep and dep not in valid_ids:
                unresolved += 1
            graph[node_id].add(dep)

    # Keep only internal edges for cycle check.
    internal_graph: Dict[str, Set[str]] = {}
    for node_id, deps in graph.items():
        internal_graph[node_id] = {d for d in deps if d in graph}
    return unresolved, self_dep, _has_cycle(internal_graph)


def score_replan_candidate(candidate: Dict[str, Any], baseline_lane_ids: List[str]) -> Dict[str, Any]:
    patch = extract_candidate_patch(candidate)
    lane_ids = _lane_ids_from_patch(patch, baseline_lane_ids)
    lane_count = len(lane_ids)
    commands_cov = _commands_coverage(patch)
    checkpoints_cov = _checkpoint_coverage(patch.get("checkpoints", []), lane_ids)
    unresolved_dep, self_dep, cycle = _dag_risk(patch.get("dag", {}), lane_ids)
    confidence = _safe_float(candidate.get("confidence", 0.5), 0.5)
    if confidence < 0.0:
        confidence = 0.0
    if confidence > 1.0:
        confidence = 1.0
    risk_penalty = _safe_float(candidate.get("risk_penalty", 0.0), 0.0)
    if risk_penalty < 0.0:
        risk_penalty = 0.0
    if risk_penalty > 40.0:
        risk_penalty = 40.0

    target_lane_count = 3
    lane_shape_penalty = abs(lane_count - target_lane_count) * 2.0
    if lane_count == 0:
        lane_shape_penalty += 20.0

    has_mutation = any(k in patch for k in ("replace_pending_lanes", "append_lanes", "update_lanes", "dag", "checkpoints", "set_limits"))
    noop_penalty = 10.0 if not has_mutation else 0.0

    score = 55.0
    score += 20.0 * commands_cov
    score += 10.0 * checkpoints_cov
    score += 8.0 * confidence
    score -= lane_shape_penalty
    score -= float(unresolved_dep) * 12.0
    score -= float(self_dep) * 10.0
    score -= 18.0 if cycle else 0.0
    score -= risk_penalty
    score -= noop_penalty
    if score < 0.0:
        score = 0.0
    if score > 100.0:
        score = 100.0

    candidate_id = str(candidate.get("id", "")).strip() or str(candidate.get("candidate_id", "")).strip() or "candidate"
    return {
        "candidate_id": candidate_id,
        "score": round(score, 3),
        "lane_count": lane_count,
        "commands_coverage": round(commands_cov, 4),
        "checkpoints_coverage": round(checkpoints_cov, 4),
        "unresolved_dependencies": unresolved_dep,
        "self_dependencies": self_dep,
        "cycle_detected": cycle,
        "risk_penalty": risk_penalty,
        "confidence": confidence,
        "reason": str(patch.get("reason", candidate.get("reason", ""))).strip(),
        "patch": patch,
    }


def select_best_replan_candidate(candidates: Any, baseline_lane_ids: List[str]) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    if not isinstance(candidates, list):
        return {"selected": {}, "selected_id": "", "selected_score": 0.0, "ranking": rows}

    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        rows.append(score_replan_candidate(raw, baseline_lane_ids))

    if not rows:
        return {"selected": {}, "selected_id": "", "selected_score": 0.0, "ranking": rows}

    rows.sort(
        key=lambda x: (
            _safe_float(x.get("score", 0.0), 0.0),
            _safe_float(x.get("commands_coverage", 0.0), 0.0),
            _safe_float(x.get("checkpoints_coverage", 0.0), 0.0),
            -_safe_float(x.get("unresolved_dependencies", 0.0), 0.0),
            -_safe_float(x.get("self_dependencies", 0.0), 0.0),
        ),
        reverse=True,
    )
    winner = rows[0]
    return {
        "selected": dict(winner.get("patch", {})),
        "selected_id": str(winner.get("candidate_id", "")),
        "selected_score": _safe_float(winner.get("score", 0.0), 0.0),
        "ranking": [{k: v for k, v in row.items() if k != "patch"} for row in rows],
    }

