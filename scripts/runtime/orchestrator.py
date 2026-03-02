#!/usr/bin/env python3
"""Independent runtime orchestrator for plan-executor."""

from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .control_plane import read_control_messages
from .event_store import EventStore
from .gate_engine import evaluate_checkpoint
from .message_bus import send_message
from .plan_search import PLAN_KEYS, select_best_replan_candidate
from .specialist_registry import load_registry, resolve_specialist
from .worker_adapters import resolve_adapter


def utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_dependency_map(nodes: List[Dict[str, Any]], lane_ids: List[str]) -> Dict[str, set[str]]:
    if not nodes:
        # Fallback to sequential dependency when DAG is absent.
        deps: Dict[str, set[str]] = {}
        for i, lane_id in enumerate(lane_ids):
            deps[lane_id] = {lane_ids[i - 1]} if i > 0 else set()
        return deps

    deps = {str(n["id"]): {str(x) for x in n.get("depends_on", [])} for n in nodes}
    for lane_id in lane_ids:
        deps.setdefault(lane_id, set())
    return deps


class RuntimeOrchestrator:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.store = EventStore(self.project_root)

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    @staticmethod
    def _safe_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _to_commands(value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        out: List[str] = []
        for item in value:
            cmd = str(item).strip()
            if cmd:
                out.append(cmd)
        return out

    def _worker_templates_by_role(self, lanes: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for lane in lanes:
            role = str(lane.get("owner_role", "")).strip().lower()
            if not role:
                continue
            out[role] = {
                "worker_id": str(lane.get("worker_id", "")),
                "worker_role": str(lane.get("worker_role", "")),
                "worker_engine": str(lane.get("worker_engine", "")),
                "worker_command_template": str(lane.get("worker_command_template", "")),
                "ai_timeout_sec": self._safe_int(lane.get("ai_timeout_sec", 180), 180),
                "ai_max_retries": self._safe_int(lane.get("ai_max_retries", 1), 1),
                "ai_backoff_sec": self._safe_float(lane.get("ai_backoff_sec", 1.5), 1.5),
                "delegate_timeout_sec": self._safe_int(lane.get("delegate_timeout_sec", 180), 180),
                "delegate_poll_sec": self._safe_float(lane.get("delegate_poll_sec", 0.3), 0.3),
            }
        return out

    def _normalize_lane_spec(
        self,
        lane_spec: Dict[str, Any],
        template_by_role: Dict[str, Dict[str, Any]],
        index_hint: int,
        default_template: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        lane_id = str(lane_spec.get("id", "")).strip() or f"lane-r{index_hint}"
        owner_role = str(lane_spec.get("owner_role", "")).strip() or str(lane_spec.get("role", "")).strip() or "unassigned"
        tpl = template_by_role.get(owner_role.lower(), dict(default_template or {}))
        return {
            "id": lane_id,
            "owner_role": owner_role,
            "scope": str(lane_spec.get("scope", "")).strip(),
            "commands": self._to_commands(lane_spec.get("commands", [])),
            "status": "pending",
            "attempts": 0,
            "error": "",
            "worker_id": str(tpl.get("worker_id", "")),
            "worker_role": str(tpl.get("worker_role", "") or owner_role),
            "worker_engine": str(tpl.get("worker_engine", "")),
            "worker_command_template": str(tpl.get("worker_command_template", "")),
            "ai_timeout_sec": self._safe_int(tpl.get("ai_timeout_sec", 180), 180),
            "ai_max_retries": self._safe_int(tpl.get("ai_max_retries", 1), 1),
            "ai_backoff_sec": self._safe_float(tpl.get("ai_backoff_sec", 1.5), 1.5),
            "delegate_timeout_sec": self._safe_int(tpl.get("delegate_timeout_sec", 180), 180),
            "delegate_poll_sec": self._safe_float(tpl.get("delegate_poll_sec", 0.3), 0.3),
        }

    def _load_json(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"missing file: {path}")
        # Accept UTF-8 with/without BOM to interop with Windows PowerShell writers.
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def _lane_index(self, state: Dict[str, Any], lane_id: str) -> int:
        for i, lane in enumerate(state["lanes"]):
            if lane["id"] == lane_id:
                return i
        raise KeyError(f"lane not found: {lane_id}")

    def _lane_status_map(self, state: Dict[str, Any]) -> Dict[str, str]:
        return {str(l["id"]): str(l.get("status", "pending")) for l in state.get("lanes", [])}

    def _ready_lanes(self, state: Dict[str, Any], dep_map: Dict[str, set[str]]) -> List[str]:
        lane_status = self._lane_status_map(state)
        ready: List[str] = []
        for lane_id in state.get("lane_order", []):
            if lane_status.get(lane_id) != "pending":
                continue
            deps = dep_map.get(lane_id, set())
            if all(lane_status.get(d) == "completed" for d in deps):
                ready.append(lane_id)
        return ready

    def _emit_message(
        self,
        run_id: str,
        from_agent: str,
        to_agent: str,
        kind: str,
        content: str,
        metadata: Dict[str, str] | None = None,
    ) -> None:
        try:
            send_message(
                project_root=self.project_root,
                run_id=run_id,
                from_agent=from_agent,
                to_agent=to_agent,
                kind=kind,
                content=content,
                metadata=metadata or {},
            )
        except Exception as exc:
            self.store.append_event(
                run_id,
                "message_error",
                {
                    "from_agent": from_agent,
                    "to_agent": to_agent,
                    "kind": kind,
                    "error": str(exc),
                },
            )

    def _merge_limits(self, state: Dict[str, Any], patch: Dict[str, Any]) -> None:
        limits = dict(state.get("limits", {}))
        for key, value in patch.items():
            limits[str(key)] = value
        state["limits"] = limits

    @staticmethod
    def _deep_merge_dict(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(base)
        for raw_key, value in patch.items():
            key = str(raw_key)
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = RuntimeOrchestrator._deep_merge_dict(dict(out.get(key, {})), value)
            else:
                out[key] = value
        return out

    @staticmethod
    def _to_ids(value: Any) -> List[str]:
        if isinstance(value, str):
            token = value.strip()
            return [token] if token else []
        if not isinstance(value, list):
            return []
        out: List[str] = []
        for item in value:
            token = str(item).strip()
            if token:
                out.append(token)
        return out

    def _apply_consensus_reconfigure_payload(self, run_id: str, state: Dict[str, Any], payload: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        checkpoints = state.get("checkpoints", [])
        if not isinstance(checkpoints, list) or not checkpoints:
            self.store.append_event(
                run_id,
                "consensus_reconfigure_noop",
                {
                    "reason": "no-checkpoints",
                    "source": "control-plane",
                },
            )
            return state, False

        row_list = payload.get("checkpoints", [])
        rows: List[Dict[str, Any]] = []
        if isinstance(row_list, list) and row_list:
            for row in row_list:
                if isinstance(row, dict):
                    rows.append(dict(row))
        else:
            rows.append(dict(payload))

        changed = False
        touched_ids: List[str] = []
        missing_ids: List[str] = []
        patch_keys: List[str] = []
        reason = str(payload.get("reason", "")).strip()

        for row in rows:
            gate_patch = row.get("consensus_gate_patch", row.get("patch", {}))
            if not isinstance(gate_patch, dict) or not gate_patch:
                continue

            replace = self._safe_int(row.get("replace", 0), 0) == 1
            if isinstance(row.get("replace"), bool):
                replace = bool(row.get("replace"))
            elif isinstance(row.get("replace"), str):
                replace = str(row.get("replace", "")).strip().lower() in {"1", "true", "yes", "y", "on"}
            patch_keys.extend([str(k) for k in gate_patch.keys()])

            direct_id = str(row.get("checkpoint_id", "")).strip()
            target_ids = self._to_ids(row.get("checkpoint_ids", []))
            if direct_id:
                target_ids.append(direct_id)
            target_ids = sorted(set([x for x in target_ids if x]))
            apply_all = len(target_ids) == 0
            found_target_ids: set[str] = set()

            for cp in checkpoints:
                if not isinstance(cp, dict):
                    continue
                cp_id = str(cp.get("id", "")).strip()
                if not apply_all and cp_id not in target_ids:
                    continue
                if cp_id:
                    found_target_ids.add(cp_id)
                current_gate = cp.get("consensus_gate", {})
                if not isinstance(current_gate, dict):
                    current_gate = {}
                new_gate = dict(gate_patch) if replace else self._deep_merge_dict(current_gate, gate_patch)
                if not isinstance(cp.get("consensus_gate", {}), dict) or new_gate != current_gate:
                    cp["consensus_gate"] = new_gate
                    changed = True
                    if cp_id:
                        touched_ids.append(cp_id)

            if not apply_all:
                for cp_id in target_ids:
                    if cp_id not in found_target_ids:
                        missing_ids.append(cp_id)

        touched_ids = sorted(set([x for x in touched_ids if x]))
        missing_ids = sorted(set([x for x in missing_ids if x]))
        patch_keys = sorted(set([x for x in patch_keys if x]))

        if changed:
            state["checkpoints"] = checkpoints
            self.store.append_event(
                run_id,
                "consensus_reconfigured",
                {
                    "source": "control-plane",
                    "reason": reason,
                    "checkpoint_ids": touched_ids,
                    "missing_checkpoint_ids": missing_ids,
                    "patch_keys": patch_keys,
                },
            )
            self._emit_message(
                run_id=run_id,
                from_agent="control-plane",
                to_agent="orchestrator",
                kind="consensus_reconfigured",
                content=f"consensus reconfigured checkpoints={len(touched_ids)}",
                metadata={
                    "checkpoint_count": str(len(touched_ids)),
                    "patch_keys": ",".join(patch_keys[:20]),
                    "missing_checkpoint_count": str(len(missing_ids)),
                },
            )
            return state, True

        self.store.append_event(
            run_id,
            "consensus_reconfigure_noop",
            {
                "source": "control-plane",
                "reason": reason or "no-effective-change",
                "missing_checkpoint_ids": missing_ids,
                "patch_keys": patch_keys,
            },
        )
        return state, False

    def _apply_replan_payload(self, run_id: str, state: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        candidate_plans = payload.get("candidate_plans", [])
        if isinstance(candidate_plans, list) and candidate_plans:
            plan_search = select_best_replan_candidate(candidate_plans, [str(x) for x in state.get("lane_order", [])])
            selected_patch = plan_search.get("selected", {})
            selected_id = str(plan_search.get("selected_id", "")).strip()
            selected_score = self._safe_float(plan_search.get("selected_score", 0.0), 0.0)
            ranking = plan_search.get("ranking", [])

            if isinstance(selected_patch, dict) and selected_patch:
                merged_payload = dict(payload)
                for key in PLAN_KEYS:
                    if key in selected_patch:
                        merged_payload[key] = selected_patch[key]
                payload = merged_payload

            policy = dict(state.get("plan_search_policy", {}))
            policy["applied_count"] = int(policy.get("applied_count", 0)) + 1
            policy["last_candidate_id"] = selected_id
            policy["last_candidate_score"] = round(selected_score, 4)
            policy["last_ranking_size"] = len(ranking) if isinstance(ranking, list) else 0
            state["plan_search_policy"] = policy

            self.store.append_event(
                run_id,
                "replan_candidate_selected",
                {
                    "selected_id": selected_id,
                    "selected_score": selected_score,
                    "ranking": ranking[:5] if isinstance(ranking, list) else [],
                },
            )
            self._emit_message(
                run_id=run_id,
                from_agent="planner",
                to_agent="orchestrator",
                kind="replan_candidate_selected",
                content=f"selected={selected_id} score={selected_score:.2f}",
                metadata={
                    "selected_id": selected_id,
                    "selected_score": f"{selected_score:.4f}",
                },
            )

        lanes = list(state.get("lanes", []))
        lane_order = [str(x) for x in state.get("lane_order", [])]
        template_by_role = self._worker_templates_by_role(lanes)
        default_template = next(iter(template_by_role.values()), {})

        lane_by_id = {str(l.get("id", "")): dict(l) for l in lanes}
        changed = False

        replace_pending = payload.get("replace_pending_lanes", [])
        if isinstance(replace_pending, list) and replace_pending:
            kept: List[Dict[str, Any]] = []
            for lane_id in lane_order:
                lane = lane_by_id.get(lane_id)
                if not lane:
                    continue
                status = str(lane.get("status", "pending"))
                if status in {"completed", "running"}:
                    kept.append(lane)

            new_pending: List[Dict[str, Any]] = []
            for i, spec in enumerate(replace_pending, start=1):
                if not isinstance(spec, dict):
                    continue
                lane = self._normalize_lane_spec(spec, template_by_role, index_hint=i, default_template=default_template)
                new_pending.append(lane)
            lanes = kept + new_pending
            lane_order = [str(l.get("id")) for l in lanes]
            lane_by_id = {str(l.get("id", "")): dict(l) for l in lanes}
            changed = True

        update_lanes = payload.get("update_lanes", [])
        if isinstance(update_lanes, list):
            for spec in update_lanes:
                if not isinstance(spec, dict):
                    continue
                lane_id = str(spec.get("id", "")).strip()
                if not lane_id or lane_id not in lane_by_id:
                    continue
                lane = lane_by_id[lane_id]
                status = str(lane.get("status", "pending"))
                if status != "pending":
                    continue
                if "owner_role" in spec or "role" in spec:
                    lane["owner_role"] = str(spec.get("owner_role", spec.get("role", lane.get("owner_role", "unassigned")))).strip()
                if "commands" in spec:
                    lane["commands"] = self._to_commands(spec.get("commands", []))
                if "scope" in spec:
                    lane["scope"] = str(spec.get("scope", lane.get("scope", ""))).strip()
                changed = True

        append_lanes = payload.get("append_lanes", [])
        if isinstance(append_lanes, list):
            for i, spec in enumerate(append_lanes, start=1):
                if not isinstance(spec, dict):
                    continue
                lane = self._normalize_lane_spec(spec, template_by_role, index_hint=1000 + i, default_template=default_template)
                lane_id = str(lane.get("id", ""))
                if lane_id in lane_by_id:
                    continue
                lanes.append(lane)
                lane_order.append(lane_id)
                lane_by_id[lane_id] = lane
                changed = True

        if changed:
            state["lanes"] = [lane_by_id[lid] for lid in lane_order if lid in lane_by_id]
            state["lane_order"] = lane_order

        dag_patch = payload.get("dag")
        if isinstance(dag_patch, dict) and isinstance(dag_patch.get("nodes"), list):
            dep_map = build_dependency_map(list(dag_patch.get("nodes", [])), [str(x) for x in state.get("lane_order", [])])
            state["dependency_map"] = {k: sorted(v) for k, v in dep_map.items()}
            changed = True
        elif changed:
            dep_map = build_dependency_map([], [str(x) for x in state.get("lane_order", [])])
            state["dependency_map"] = {k: sorted(v) for k, v in dep_map.items()}

        checkpoints = payload.get("checkpoints")
        if isinstance(checkpoints, list):
            state["checkpoints"] = checkpoints
            state["checkpoint_status"] = {}
            changed = True

        set_limits = payload.get("set_limits")
        if isinstance(set_limits, dict):
            self._merge_limits(state, set_limits)
            changed = True

        if changed:
            self.store.append_event(
                run_id,
                "replan_applied",
                {
                    "lane_count": len(state.get("lanes", [])),
                    "reason": str(payload.get("reason", "")).strip(),
                },
            )
            self._emit_message(
                run_id=run_id,
                from_agent="integrator",
                to_agent="orchestrator",
                kind="replan_applied",
                content=f"replan applied; lanes={len(state.get('lanes', []))}",
                metadata={"lane_count": str(len(state.get("lanes", [])))},
            )
        return state

    def _apply_control_messages(self, run_id: str, state: Dict[str, Any], limit: int = 100) -> Tuple[Dict[str, Any], bool]:
        offset = self._safe_int(state.get("control_offset", 0), 0)
        rows, new_offset = read_control_messages(self.project_root, run_id, offset=offset, limit=max(1, limit))
        if not rows and new_offset == offset:
            return state, True

        processed_ids = set([str(x) for x in state.get("control_processed_ids", []) if str(x).strip()])
        changed = False
        for row in rows:
            msg_id = str(row.get("msg_id", "")).strip()
            if msg_id and msg_id in processed_ids:
                continue
            kind = str(row.get("kind", "note")).strip().lower() or "note"
            payload = row.get("payload", {})
            if not isinstance(payload, dict):
                payload = {"value": payload}
            self.store.append_event(
                run_id,
                "control_message_received",
                {
                    "msg_id": msg_id,
                    "kind": kind,
                    "source": str(row.get("source", "")),
                },
            )
            if kind == "abort":
                reason = str(payload.get("reason", "control-plane-abort")).strip() or "control-plane-abort"
                state["status"] = "aborted"
                self.store.append_event(run_id, "abort", {"reason": reason, "source": "control-plane"})
                self._emit_message(
                    run_id=run_id,
                    from_agent="control-plane",
                    to_agent="all",
                    kind="abort",
                    content="Run aborted by control message.",
                    metadata={"reason": reason},
                )
                changed = True
                break
            if kind == "replan":
                state = self._apply_replan_payload(run_id, state, payload)
                changed = True
            if kind in {"consensus_reconfigure", "consensus-reconfigure", "consensus_patch", "consensus-patch"}:
                state, consensus_changed = self._apply_consensus_reconfigure_payload(run_id, state, payload)
                changed = changed or consensus_changed

            if msg_id:
                processed_ids.add(msg_id)

        state["control_offset"] = max(offset, new_offset)
        state["control_processed_ids"] = sorted(processed_ids)[-500:]
        if changed:
            self.store.write_state(run_id, state)
        else:
            self.store.write_state(run_id, state)
        return state, state.get("status") != "aborted"

    @staticmethod
    def _has_ai_skip(evidence: List[str]) -> bool:
        for item in evidence:
            token = str(item).strip().lower()
            if token == "ai-worker-unavailable-skip":
                return True
        return False

    def _apply_ai_skip_policy(
        self,
        run_id: str,
        state: Dict[str, Any],
        lane_id: str,
        lane_status: str,
        evidence: List[str],
    ) -> Tuple[Dict[str, Any], bool]:
        limits = state.get("limits", {})
        warn_streak = int(limits.get("ai_worker_skip_warn_streak", 2))
        fail_streak = int(limits.get("ai_worker_skip_fail_streak", 0))

        policy = dict(
            state.get(
                "ai_worker_policy",
                {
                    "skip_streak": 0,
                    "skip_total": 0,
                    "warning_count": 0,
                    "last_warning_streak": 0,
                },
            )
        )
        is_skip = lane_status == "pass" and self._has_ai_skip(evidence)
        if is_skip:
            policy["skip_streak"] = int(policy.get("skip_streak", 0)) + 1
            policy["skip_total"] = int(policy.get("skip_total", 0)) + 1
        else:
            policy["skip_streak"] = 0

        current_streak = int(policy.get("skip_streak", 0))
        state["ai_worker_policy"] = policy

        if warn_streak > 0 and current_streak >= warn_streak:
            last_warning_streak = int(policy.get("last_warning_streak", 0))
            if current_streak > last_warning_streak:
                policy["last_warning_streak"] = current_streak
                policy["warning_count"] = int(policy.get("warning_count", 0)) + 1
                self.store.append_event(
                    run_id,
                    "warning",
                    {
                        "type": "ai-worker-skip-streak",
                        "lane_id": lane_id,
                        "skip_streak": current_streak,
                        "warn_threshold": warn_streak,
                    },
                )
                self._emit_message(
                    run_id=run_id,
                    from_agent="orchestrator",
                    to_agent="integrator",
                    kind="warning",
                    content=f"ai-worker skip streak={current_streak}",
                    metadata={
                        "lane_id": lane_id,
                        "skip_streak": str(current_streak),
                        "warn_threshold": str(warn_streak),
                    },
                )

        if fail_streak > 0 and current_streak >= fail_streak:
            state["status"] = "failed"
            self.store.append_event(
                run_id,
                "checkpoint",
                {
                    "status": "fail",
                    "reason": "ai-worker-skip-streak-exceeded",
                    "lane_id": lane_id,
                    "skip_streak": current_streak,
                    "fail_threshold": fail_streak,
                },
            )
            self._emit_message(
                run_id=run_id,
                from_agent="integrator",
                to_agent="orchestrator",
                kind="failure",
                content="Run failed due to ai-worker skip streak policy.",
                metadata={
                    "reason": "ai-worker-skip-streak-exceeded",
                    "lane_id": lane_id,
                    "skip_streak": str(current_streak),
                    "fail_threshold": str(fail_streak),
                },
            )
            self.store.write_state(run_id, state)
            return state, False

        self.store.write_state(run_id, state)
        return state, True

    def _evaluate_ready_checkpoints(self, run_id: str, state: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        checkpoints = state.get("checkpoints", [])
        if not checkpoints:
            return state, True

        cp_status: Dict[str, str] = dict(state.get("checkpoint_status", {}))
        lane_status = self._lane_status_map(state)
        changed = False
        for checkpoint in checkpoints:
            cp_id = str(checkpoint.get("id", f"checkpoint-{len(cp_status)+1}"))
            if cp_status.get(cp_id) in {"pass", "fail"}:
                continue
            after_lanes = [str(x) for x in checkpoint.get("after_lanes", [])]
            if after_lanes and not all(lane_status.get(l) == "completed" for l in after_lanes):
                continue
            if not after_lanes:
                # No after_lanes means evaluate only at full completion.
                if not all(status == "completed" for status in lane_status.values()):
                    continue

            result = evaluate_checkpoint(
                checkpoint,
                self.project_root,
                run_id=run_id,
                limits={
                    **(state.get("limits", {}) if isinstance(state.get("limits", {}), dict) else {}),
                    "guardrail_environment": str(state.get("guardrail_environment", "")).strip(),
                },
            )
            cp_status[cp_id] = result.status
            changed = True
            self.store.append_event(
                run_id,
                "checkpoint",
                {
                    "checkpoint_id": cp_id,
                    "status": result.status,
                    "after_lanes": after_lanes,
                    "evidence": result.evidence,
                    "commands": result.commands,
                    "error": result.error,
                },
            )
            self._emit_message(
                run_id=run_id,
                from_agent="integrator",
                to_agent="orchestrator",
                kind="checkpoint",
                content=f"{cp_id} status={result.status}",
                metadata={
                    "checkpoint_id": cp_id,
                    "status": result.status,
                },
            )
            if result.status != "pass":
                state["status"] = "failed"
                state["checkpoint_status"] = cp_status
                self.store.write_state(run_id, state)
                return state, False

        if changed:
            state["checkpoint_status"] = cp_status
            self.store.write_state(run_id, state)
        return state, True

    def start(
        self,
        runbook_path: Path,
        manifest_path: Path | None,
        adapter_name: str,
        run_id: str | None = None,
        engine: str = "shell",
    ) -> Dict[str, Any]:
        runbook = self._load_json(runbook_path)
        manifest = self._load_json(manifest_path) if manifest_path else {}
        if not run_id:
            run_id = f"run-{utc_compact()}-{uuid.uuid4().hex[:6]}"

        existing = self.store.read_state(run_id)
        if existing:
            raise ValueError(f"run already exists: {run_id}")

        manifest_adapter = manifest.get("meta", {}).get("adapter", "")
        effective_adapter_name = manifest_adapter or adapter_name
        adapter, notes = resolve_adapter(effective_adapter_name)
        if not effective_adapter_name:
            effective_adapter_name = adapter.name

        lanes_src = runbook.get("lanes", [])
        lane_ids = [str(l.get("id")) for l in lanes_src]
        dep_map = build_dependency_map(runbook.get("dag", {}).get("nodes", []), lane_ids)
        lane_order = lane_ids[:]
        workers = manifest.get("workers", [])
        if not isinstance(workers, list):
            workers = []

        lanes = []
        lane_by_id = {str(lane["id"]): lane for lane in lanes_src}
        for idx, lane_id in enumerate(lane_order):
            lane_src = lane_by_id.get(lane_id, {"id": lane_id})
            owner_role = str(lane_src.get("owner_role", "unassigned"))
            owner_role_lc = owner_role.lower()
            assigned_worker: Dict[str, Any] = {}
            for worker in workers:
                if str(worker.get("role", "")).strip().lower() == owner_role_lc:
                    assigned_worker = dict(worker)
                    break
            if not assigned_worker and idx < len(workers):
                assigned_worker = dict(workers[idx])

            lanes.append(
                {
                    "id": lane_id,
                    "owner_role": owner_role,
                    "scope": str(lane_src.get("scope", "")).strip(),
                    "commands": self._to_commands(lane_src.get("commands", [])),
                    "status": "pending",
                    "attempts": 0,
                    "error": "",
                    "worker_id": str(assigned_worker.get("id", "")),
                    "worker_role": str(assigned_worker.get("role", "")),
                    "worker_engine": str(assigned_worker.get("engine", "")) or engine,
                    "worker_command_template": str(assigned_worker.get("command_template", "")),
                    "ai_timeout_sec": self._safe_int(assigned_worker.get("timeout_sec", 180), 180),
                    "ai_max_retries": self._safe_int(assigned_worker.get("max_retries", 1), 1),
                    "ai_backoff_sec": self._safe_float(assigned_worker.get("backoff_sec", 1.5), 1.5),
                    "delegate_timeout_sec": self._safe_int(
                        assigned_worker.get("delegate_timeout_sec", assigned_worker.get("timeout_sec", 180)),
                        180,
                    ),
                    "delegate_poll_sec": self._safe_float(
                        assigned_worker.get("delegate_poll_sec", assigned_worker.get("poll_sec", 0.3)),
                        0.3,
                    ),
                }
            )

        specialists = load_registry(self.project_root)
        unknown_owner_roles: List[str] = []
        for lane in lanes:
            owner_role = str(lane.get("owner_role", "unassigned"))
            if owner_role == "unassigned":
                continue
            if not resolve_specialist(self.project_root, owner_role):
                unknown_owner_roles.append(owner_role)

        mode = str(runbook.get("meta", {}).get("mode", "sequential"))
        max_parallel_workers = int(runbook.get("meta", {}).get("max_parallel_workers", 1 if mode == "sequential" else 4))
        if max_parallel_workers < 1:
            max_parallel_workers = 1
        guardrail_env = str(runbook.get("meta", {}).get("environment", "")).strip().lower()
        if not guardrail_env:
            profile = str(runbook.get("meta", {}).get("profile", "")).strip().lower()
            if profile == "speed":
                guardrail_env = "dev"
            elif profile == "balanced":
                guardrail_env = "ci"
            elif profile == "hardening":
                guardrail_env = "prod"
        raw_guardrails = runbook.get("limits", {}).get("command_guardrails", {})
        if not guardrail_env and isinstance(raw_guardrails, dict):
            guardrail_env = str(raw_guardrails.get("environment", "")).strip().lower()

        state: Dict[str, Any] = {
            "run_id": run_id,
            "status": "running",
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "runbook_path": str(runbook_path.resolve()),
            "manifest_path": str(manifest_path.resolve()) if manifest_path else "",
            "adapter": adapter.name,
            "adapter_notes": notes,
            "lane_order": lane_order,
            "dependency_map": {k: sorted(v) for k, v in dep_map.items()},
            "max_parallel_workers": max_parallel_workers,
            "current_lane_index": 0,
            "lanes": lanes,
            "checkpoints": runbook.get("checkpoints", []),
            "checkpoint_status": {},
            "limits": runbook.get("limits", {}),
            "guardrail_environment": guardrail_env,
            "control_offset": 0,
            "control_processed_ids": [],
            "ai_worker_policy": {
                "skip_streak": 0,
                "skip_total": 0,
                "warning_count": 0,
                "last_warning_streak": 0,
            },
            "plan_search_policy": {
                "applied_count": 0,
                "last_candidate_id": "",
                "last_candidate_score": 0.0,
                "last_ranking_size": 0,
            },
            "specialist_count": len(specialists),
            "unknown_owner_roles": sorted(set(unknown_owner_roles)),
            "manifest_worker_count": len(workers),
        }
        self.store.write_state(run_id, state)
        self.store.append_event(
            run_id,
            "preflight",
            {
                "adapter": adapter.name,
                "adapter_notes": notes,
                "runbook": str(runbook_path.resolve()),
                "manifest": str(manifest_path.resolve()) if manifest_path else "",
                "lanes": lane_order,
                "max_parallel_workers": max_parallel_workers,
                "guardrail_environment": guardrail_env,
                "specialist_count": len(specialists),
                "unknown_owner_roles": sorted(set(unknown_owner_roles)),
                "manifest_worker_count": len(workers),
            },
        )
        if unknown_owner_roles:
            self.store.append_event(
                run_id,
                "preflight_warning",
                {"unknown_owner_roles": sorted(set(unknown_owner_roles))},
            )

        for lane in lanes:
            self._emit_message(
                run_id=run_id,
                from_agent="orchestrator",
                to_agent=str(lane.get("owner_role", "unassigned")),
                kind="lane_assignment",
                content=f"Assigned lane {lane.get('id')}",
                metadata={
                    "lane_id": str(lane.get("id")),
                    "owner_role": str(lane.get("owner_role", "unassigned")),
                    "adapter": adapter.name,
                    "worker_id": str(lane.get("worker_id", "")),
                    "worker_engine": str(lane.get("worker_engine", "")),
                },
            )

        return self._run_loop(run_id, adapter)

    def _run_loop(self, run_id: str, adapter: Any) -> Dict[str, Any]:
        state = self.store.read_state(run_id)
        if not state:
            raise RuntimeError(f"missing state for run: {run_id}")

        while True:
            latest_state = self.store.read_state(run_id) or {}
            if latest_state.get("status") == "aborted":
                return latest_state
            state = latest_state

            state, keep_running = self._apply_control_messages(run_id, state)
            if not keep_running:
                return state

            state, ok = self._evaluate_ready_checkpoints(run_id, state)
            if not ok:
                return state

            dep_map = {str(k): {str(x) for x in v} for k, v in state.get("dependency_map", {}).items()}
            max_workers = int(state.get("max_parallel_workers", 1))
            if max_workers < 1:
                max_workers = 1

            lane_status = self._lane_status_map(state)
            if all(status == "completed" for status in lane_status.values()):
                state["status"] = "completed"
                self.store.write_state(run_id, state)
                self.store.append_event(
                    run_id,
                    "finalize",
                    {
                        "status": "completed",
                        "lane_count": len(state["lanes"]),
                    },
                )
                self._emit_message(
                    run_id=run_id,
                    from_agent="orchestrator",
                    to_agent="all",
                    kind="finalize",
                    content="Run completed",
                    metadata={
                        "status": "completed",
                        "lane_count": str(len(state["lanes"])),
                    },
                )
                return state

            ready = self._ready_lanes(state, dep_map)
            if not ready:
                # No runnable lane and not completed means dependency deadlock or prior failure.
                if any(status == "failed" for status in lane_status.values()):
                    state["status"] = "failed"
                    self.store.write_state(run_id, state)
                    self._emit_message(
                        run_id=run_id,
                        from_agent="integrator",
                        to_agent="orchestrator",
                        kind="failure",
                        content="Run failed because at least one lane is failed.",
                        metadata={"reason": "lane-failed"},
                    )
                    return state
                state["status"] = "failed"
                self.store.write_state(run_id, state)
                self.store.append_event(
                    run_id,
                    "checkpoint",
                    {
                        "status": "fail",
                        "reason": "no-ready-lanes-deadlock",
                    },
                )
                self._emit_message(
                    run_id=run_id,
                    from_agent="integrator",
                    to_agent="orchestrator",
                    kind="failure",
                    content="Run failed due to no-ready-lanes deadlock.",
                    metadata={"reason": "no-ready-lanes-deadlock"},
                )
                return state

            batch_ids = ready[:max_workers]
            for lane_id in batch_ids:
                lane_idx = self._lane_index(state, lane_id)
                lane_state = state["lanes"][lane_idx]
                lane_state["status"] = "running"
                lane_state["attempts"] = int(lane_state.get("attempts", 0)) + 1
                self.store.append_event(
                    run_id,
                    "lane_start",
                    {
                        "lane_id": lane_id,
                        "owner_role": lane_state.get("owner_role", "unassigned"),
                        "attempt": lane_state["attempts"],
                        "worker_id": lane_state.get("worker_id", ""),
                        "worker_role": lane_state.get("worker_role", ""),
                        "worker_engine": lane_state.get("worker_engine", ""),
                        "ai_timeout_sec": self._safe_int(lane_state.get("ai_timeout_sec", 180), 180),
                        "ai_max_retries": self._safe_int(lane_state.get("ai_max_retries", 1), 1),
                        "ai_backoff_sec": self._safe_float(lane_state.get("ai_backoff_sec", 1.5), 1.5),
                        "delegate_timeout_sec": self._safe_int(lane_state.get("delegate_timeout_sec", 180), 180),
                        "delegate_poll_sec": self._safe_float(lane_state.get("delegate_poll_sec", 0.3), 0.3),
                    },
                )
                self._emit_message(
                    run_id=run_id,
                    from_agent="orchestrator",
                    to_agent=str(lane_state.get("owner_role", "unassigned")),
                    kind="lane_start",
                    content=f"Start lane {lane_id}",
                    metadata={
                        "lane_id": lane_id,
                        "attempt": str(lane_state["attempts"]),
                        "worker_id": str(lane_state.get("worker_id", "")),
                        "worker_engine": str(lane_state.get("worker_engine", "")),
                        "ai_timeout_sec": str(self._safe_int(lane_state.get("ai_timeout_sec", 180), 180)),
                        "ai_max_retries": str(self._safe_int(lane_state.get("ai_max_retries", 1), 1)),
                        "delegate_timeout_sec": str(self._safe_int(lane_state.get("delegate_timeout_sec", 180), 180)),
                    },
                )
                self.store.append_event(
                    run_id,
                    "heartbeat",
                    {
                        "lane_id": lane_id,
                        "phase": "before-run",
                    },
                )
            self.store.write_state(run_id, state)

            futures = {}
            with ThreadPoolExecutor(max_workers=len(batch_ids)) as executor:
                for lane_id in batch_ids:
                    lane_idx = self._lane_index(state, lane_id)
                    lane_state = state["lanes"][lane_idx]
                    lane_payload = {
                        "id": lane_id,
                        "owner_role": lane_state.get("owner_role", "unassigned"),
                        "scope": lane_state.get("scope", ""),
                        "commands": self._to_commands(lane_state.get("commands", [])),
                    }
                    limits = state.get("limits", {})
                    lane_payload["_runtime"] = {
                        "run_id": run_id,
                        "lane_id": lane_id,
                        "attempt": int(lane_state.get("attempts", 1)),
                        "worker_id": str(lane_state.get("worker_id", "")),
                        "worker_role": str(lane_state.get("worker_role", "")),
                        "worker_engine": str(lane_state.get("worker_engine", "")),
                        "worker_command_template": str(lane_state.get("worker_command_template", "")),
                        "ai_timeout_sec": self._safe_int(lane_state.get("ai_timeout_sec", 180), 180),
                        "ai_max_retries": self._safe_int(lane_state.get("ai_max_retries", 1), 1),
                        "ai_backoff_sec": self._safe_float(lane_state.get("ai_backoff_sec", 1.5), 1.5),
                        "delegate_timeout_sec": self._safe_int(lane_state.get("delegate_timeout_sec", 180), 180),
                        "delegate_poll_sec": self._safe_float(lane_state.get("delegate_poll_sec", 0.3), 0.3),
                        "guardrail_environment": str(state.get("guardrail_environment", "")).strip(),
                        "command_guardrails": (
                            dict(limits.get("command_guardrails", {}))
                            if isinstance(limits.get("command_guardrails", {}), dict)
                            else {}
                        ),
                        "max_replan": self._safe_int(limits.get("max_replan", 2), 2),
                        "fallback_chain": limits.get("fallback_chain", ""),
                    }
                    futures[executor.submit(adapter.run_lane, lane_payload, self.project_root)] = lane_id

                failed = False
                for future in as_completed(futures):
                    lane_id = futures[future]
                    lane_idx = self._lane_index(state, lane_id)
                    lane_state = state["lanes"][lane_idx]
                    try:
                        result = future.result()
                    except Exception as exc:
                        lane_state["status"] = "failed"
                        lane_state["error"] = str(exc)
                        self.store.append_event(
                            run_id,
                            "lane_done",
                            {
                                "lane_id": lane_id,
                                "status": "fail",
                                "evidence": [],
                                "error": str(exc),
                                "commands": [],
                            },
                        )
                        self._emit_message(
                            run_id=run_id,
                            from_agent=str(lane_state.get("owner_role", "unassigned")),
                            to_agent="integrator",
                            kind="lane_done",
                            content=f"Lane {lane_id} failed with exception",
                            metadata={
                                "lane_id": lane_id,
                                "status": "fail",
                                "error": str(exc)[:500],
                                "worker_id": str(lane_state.get("worker_id", "")),
                                "worker_engine": str(lane_state.get("worker_engine", "")),
                            },
                        )
                        state, _ = self._apply_ai_skip_policy(
                            run_id=run_id,
                            state=state,
                            lane_id=lane_id,
                            lane_status="fail",
                            evidence=[],
                        )
                        failed = True
                        continue

                    if result.status == "pass":
                        lane_state["status"] = "completed"
                        lane_state["error"] = ""
                        self.store.append_event(
                            run_id,
                            "lane_done",
                            {
                                "lane_id": lane_id,
                                "status": "pass",
                                "evidence": result.evidence,
                                "commands": result.commands,
                            },
                        )
                        self._emit_message(
                            run_id=run_id,
                            from_agent=str(lane_state.get("owner_role", "unassigned")),
                            to_agent="integrator",
                            kind="lane_done",
                            content=f"Lane {lane_id} completed",
                            metadata={
                                "lane_id": lane_id,
                                "status": "pass",
                                "evidence": "|".join(result.evidence[:5]),
                                "worker_id": str(lane_state.get("worker_id", "")),
                                "worker_engine": str(lane_state.get("worker_engine", "")),
                            },
                        )
                        state, proceed = self._apply_ai_skip_policy(
                            run_id=run_id,
                            state=state,
                            lane_id=lane_id,
                            lane_status="pass",
                            evidence=[str(x) for x in result.evidence],
                        )
                        if not proceed:
                            return state
                    else:
                        lane_state["status"] = "failed"
                        lane_state["error"] = result.error
                        self.store.append_event(
                            run_id,
                            "lane_done",
                            {
                                "lane_id": lane_id,
                                "status": "fail",
                                "evidence": result.evidence,
                                "error": result.error,
                                "commands": result.commands,
                            },
                        )
                        self._emit_message(
                            run_id=run_id,
                            from_agent=str(lane_state.get("owner_role", "unassigned")),
                            to_agent="integrator",
                            kind="lane_done",
                            content=f"Lane {lane_id} failed",
                            metadata={
                                "lane_id": lane_id,
                                "status": "fail",
                                "error": str(result.error)[:500],
                                "worker_id": str(lane_state.get("worker_id", "")),
                                "worker_engine": str(lane_state.get("worker_engine", "")),
                            },
                        )
                        state, _ = self._apply_ai_skip_policy(
                            run_id=run_id,
                            state=state,
                            lane_id=lane_id,
                            lane_status="fail",
                            evidence=[str(x) for x in result.evidence],
                        )
                        failed = True

                    self.store.append_event(
                        run_id,
                        "heartbeat",
                        {
                            "lane_id": lane_id,
                            "phase": "after-run",
                        },
                    )

            # Track progress pointer as count of completed lanes.
            completed_count = sum(1 for lane in state["lanes"] if lane.get("status") == "completed")
            state["current_lane_index"] = completed_count
            if failed:
                state["status"] = "failed"
                self.store.write_state(run_id, state)
                self.store.append_event(
                    run_id,
                    "checkpoint",
                    {
                        "status": "fail",
                        "reason": "lane failure",
                    },
                )
                self._emit_message(
                    run_id=run_id,
                    from_agent="integrator",
                    to_agent="orchestrator",
                    kind="failure",
                    content="Run failed due to lane failure.",
                    metadata={"reason": "lane failure"},
                )
                return state

            self.store.write_state(run_id, state)

    def status(self, run_id: str, limit: int = 20) -> Dict[str, Any]:
        state = self.store.read_state(run_id)
        if not state:
            raise ValueError(f"run not found: {run_id}")
        events = self.store.read_events(run_id, limit=limit)
        return {
            "state": state,
            "events": events,
        }

    def resume(self, run_id: str) -> Dict[str, Any]:
        state = self.store.read_state(run_id)
        if not state:
            raise ValueError(f"run not found: {run_id}")
        if state.get("status") == "completed":
            return state
        if state.get("status") == "aborted":
            raise ValueError("cannot resume aborted run")

        # Reset failed lanes to pending for retry.
        for lane in state.get("lanes", []):
            if lane.get("status") == "failed":
                lane["status"] = "pending"
                lane["error"] = ""
        state["status"] = "running"
        self.store.write_state(run_id, state)

        adapter_name = state.get("adapter", "inline-worker")
        adapter, notes = resolve_adapter(adapter_name)
        if notes:
            self.store.append_event(run_id, "adapter_note", {"notes": notes})

        self.store.append_event(run_id, "resume", {"from_index": state.get("current_lane_index", 0)})
        self._emit_message(
            run_id=run_id,
            from_agent="orchestrator",
            to_agent="all",
            kind="resume",
            content="Run resumed",
            metadata={"from_index": str(state.get("current_lane_index", 0))},
        )
        return self._run_loop(run_id, adapter)

    def abort(self, run_id: str, reason: str) -> Dict[str, Any]:
        state = self.store.read_state(run_id)
        if not state:
            raise ValueError(f"run not found: {run_id}")
        if state.get("status") in {"completed", "aborted"}:
            return state
        state["status"] = "aborted"
        self.store.write_state(run_id, state)
        self.store.append_event(run_id, "abort", {"reason": reason})
        self._emit_message(
            run_id=run_id,
            from_agent="orchestrator",
            to_agent="all",
            kind="abort",
            content="Run aborted",
            metadata={"reason": reason},
        )
        return state

    def list_runs(self) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        for run_id in self.store.list_runs():
            state = self.store.read_state(run_id)
            if state:
                out.append((run_id, state.get("status", "unknown")))
        return out
