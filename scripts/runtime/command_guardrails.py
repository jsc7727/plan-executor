#!/usr/bin/env python3
"""Command guardrail policy for lane/gate shell execution."""

from __future__ import annotations

import fnmatch
import json
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
import shlex
from typing import Any, Dict, List, Tuple

from .code_intelligence import normalize_code_intelligence_policy


_PROFILE_PRESETS: Dict[str, Dict[str, Any]] = {
    "dev": {
        "mode": "human-approval",
        "allowlist_patterns": [],
        "denylist_patterns": [
            r"\bgit\s+reset\s+--hard\b",
            r"\bgit\s+clean\s+-fdx?\b",
            r"\brm\s+-rf\b",
            r"\bdel\s+/f\s+/s\s+/q\b",
            r"\bformat(-volume)?\b",
        ],
        "phases": ["lane", "gate"],
        "case_sensitive": False,
    },
    "ci": {
        "mode": "enforce",
        "allowlist_patterns": [],
        "denylist_patterns": [
            r"\bgit\s+reset\s+--hard\b",
            r"\bgit\s+clean\s+-fdx?\b",
            r"\brm\s+-rf\b",
            r"\bdel\s+/f\s+/s\s+/q\b",
            r"\bformat(-volume)?\b",
        ],
        "phases": ["lane", "gate"],
        "case_sensitive": False,
    },
    "prod": {
        "mode": "enforce",
        "allowlist_patterns": [
            r"^echo\b",
            r"^python(\.exe)?\s+-c\b",
            r"^python(\.exe)?\s+-m\b",
            r"^python(\.exe)?\s+[\w./\\-]+\.py\b",
            r"^pytest\b",
            r"^uv\b",
            r"^ruff\b",
            r"^mypy\b",
            r"^npm\b",
            r"^pnpm\b",
            r"^node\b",
            r"^git\s+(status|diff|show|log)\b",
            r"^powershell(\.exe)?\b",
            r"^pwsh(\.exe)?\b",
        ],
        "denylist_patterns": [
            r"\bgit\s+reset\s+--hard\b",
            r"\bgit\s+clean\s+-fdx?\b",
            r"\brm\s+-rf\b",
            r"\bdel\s+/f\s+/s\s+/q\b",
            r"\bformat(-volume)?\b",
        ],
        "phases": ["lane", "gate"],
        "case_sensitive": False,
    },
}

_OS_DENY_TEMPLATES: Dict[str, List[str]] = {
    "common": [
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bhalt\b",
        r"\bpoweroff\b",
        r"\bmkfs(\.\w+)?\b",
    ],
    "windows": [
        r"\bremove-item\b.*-recurse\b.*-force\b",
        r"\bclear-disk\b",
        r"\bformat-volume\b",
        r"\bstop-computer\b",
        r"\brestart-computer\b",
    ],
    "linux": [
        r"\bsudo\s+rm\s+-rf\b",
        r"\bchown\s+-R\s+root\b",
    ],
    "darwin": [
        r"\bdiskutil\s+erase(disk|volume)\b",
    ],
}


def _safe_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "y", "on"}:
        return True
    if token in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _to_list(value: Any) -> List[str]:
    if isinstance(value, str):
        token = value.strip()
        return [token] if token else []
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for row in value:
        token = str(row).strip()
        if token:
            out.append(token)
    return out


def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for item in items:
        token = str(item).strip()
        if not token:
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _normalize_os_template(value: Any) -> str:
    token = str(value).strip().lower()
    if token in {"win", "windows"}:
        return "windows"
    if token in {"linux", "lin"}:
        return "linux"
    if token in {"darwin", "mac", "macos", "osx"}:
        return "darwin"
    if token in {"common"}:
        return "common"
    if token in {"", "auto", "default"}:
        sys_name = platform.system().strip().lower()
        if "win" in sys_name:
            return "windows"
        if "linux" in sys_name:
            return "linux"
        if "darwin" in sys_name or "mac" in sys_name:
            return "darwin"
        return "common"
    return "common"


def known_guardrail_profiles() -> List[str]:
    return sorted(_PROFILE_PRESETS.keys())


def known_os_templates() -> List[str]:
    return sorted(set(_OS_DENY_TEMPLATES.keys()) | {"auto"})


def _to_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for raw_key, value in patch.items():
        key = str(raw_key)
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(dict(out.get(key, {})), value)
        else:
            out[key] = value
    return out


def _strip_policy_meta(policy: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(policy)
    out.pop("role_policies", None)
    out.pop("environment_policies", None)
    return out


def resolve_guardrail_policy_for_context(raw_policy: Any, role: str = "", environment: str = "") -> Dict[str, Any]:
    base = _to_dict(raw_policy)
    role_lc = str(role).strip().lower()
    env = str(environment).strip().lower()
    if not env:
        env = str(base.get("environment", "")).strip().lower()

    merged = _strip_policy_meta(base)
    sources: List[str] = []

    env_policies = _to_dict(base.get("environment_policies", {}))
    if env and isinstance(env_policies.get(env), dict):
        merged = _deep_merge(merged, dict(env_policies.get(env, {})))
        sources.append(f"environment:{env}")

    role_policies = _to_dict(base.get("role_policies", {}))
    if role_lc and isinstance(role_policies.get(role_lc), dict):
        merged = _deep_merge(merged, dict(role_policies.get(role_lc, {})))
        sources.append(f"role:{role_lc}")

    norm = normalize_guardrail_policy(merged)
    norm["resolved_role"] = role_lc
    norm["resolved_environment"] = env
    norm["resolved_sources"] = sources
    return norm


def _normalize_mode(value: Any) -> str:
    token = str(value).strip().lower()
    if token in {"audit", "enforce", "human-approval"}:
        return token
    if token in {"human_approval", "approval", "approve", "prompt"}:
        return "human-approval"
    return "enforce"


def _normalize_allow_deny_decision(value: Any, default: str = "deny") -> str:
    token = str(value).strip().lower()
    if token in {"allow", "approve", "approved", "pass", "yes", "y"}:
        return "allow"
    if token in {"deny", "reject", "block", "no", "n"}:
        return "deny"
    return default


def normalize_guardrail_policy(raw_policy: Any) -> Dict[str, Any]:
    policy = dict(raw_policy or {}) if isinstance(raw_policy, dict) else {}
    profile = str(policy.get("profile", "")).strip().lower()
    profile_cfg = dict(_PROFILE_PRESETS.get(profile, {}))
    allow = _to_list(profile_cfg.get("allowlist_patterns", [])) + _to_list(policy.get("allowlist_patterns", policy.get("allow_patterns", [])))

    os_template_raw = policy.get("os_template", profile_cfg.get("os_template", "auto"))
    os_template = _normalize_os_template(os_template_raw)
    include_os_risky = _safe_bool(policy.get("include_os_risky_denylist", True), True)
    os_deny: List[str] = []
    if include_os_risky:
        os_deny.extend(_to_list(_OS_DENY_TEMPLATES.get("common", [])))
        if os_template != "common":
            os_deny.extend(_to_list(_OS_DENY_TEMPLATES.get(os_template, [])))

    deny = _to_list(profile_cfg.get("denylist_patterns", [])) + os_deny + _to_list(
        policy.get("denylist_patterns", policy.get("deny_patterns", []))
    )
    allow = _dedupe_keep_order(allow)
    deny = _dedupe_keep_order(deny)

    enabled_default = bool(allow or deny)
    enabled = _safe_bool(policy.get("enabled", profile_cfg.get("enabled", enabled_default)), enabled_default)
    mode = _normalize_mode(policy.get("mode", profile_cfg.get("mode", "enforce")))
    case_sensitive = _safe_bool(policy.get("case_sensitive", profile_cfg.get("case_sensitive", False)), False)
    raw_phases = policy.get("phases", profile_cfg.get("phases", ["lane", "gate"]))
    phases = [x.strip().lower() for x in _to_list(raw_phases) if x.strip()]
    if not phases:
        phases = ["lane", "gate"]
    approval_prompt = _safe_bool(policy.get("approval_prompt", True), True)
    approval_non_interactive_decision = _normalize_allow_deny_decision(
        policy.get("approval_non_interactive_decision", "deny"),
        default="deny",
    )
    approval_auto_allow_patterns = _dedupe_keep_order(_to_list(policy.get("approval_auto_allow_patterns", [])))
    approval_log_enabled = _safe_bool(policy.get("approval_log_enabled", True), True)
    approval_safe_paths = _dedupe_keep_order(_to_list(policy.get("approval_safe_paths", [])))
    approval_safe_path_prefixes = _dedupe_keep_order(_to_list(policy.get("approval_safe_path_prefixes", [])))
    approval_safe_path_globs = _dedupe_keep_order(_to_list(policy.get("approval_safe_path_globs", [])))
    approval_auto_allow_safe_delete = _safe_bool(policy.get("approval_auto_allow_safe_delete", True), True)
    environment = str(policy.get("environment", "")).strip().lower()
    code_intel_raw = dict(policy.get("code_intelligence", {})) if isinstance(policy.get("code_intelligence", {}), dict) else {}
    code_intel_flat: Dict[str, Any] = {}
    if "code_intelligence_enabled" in policy:
        code_intel_flat["enabled"] = policy.get("code_intelligence_enabled")
    if "code_intelligence_mode" in policy:
        code_intel_flat["mode"] = policy.get("code_intelligence_mode")
    if "code_intelligence_max_total_code_files" in policy:
        code_intel_flat["max_total_code_files"] = policy.get("code_intelligence_max_total_code_files")
    if "code_intelligence_max_high_risk_files" in policy:
        code_intel_flat["max_high_risk_files"] = policy.get("code_intelligence_max_high_risk_files")
    if "code_intelligence_high_risk_symbol_threshold" in policy:
        code_intel_flat["high_risk_symbol_threshold"] = policy.get("code_intelligence_high_risk_symbol_threshold")
    if "code_intelligence_high_risk_import_threshold" in policy:
        code_intel_flat["high_risk_import_threshold"] = policy.get("code_intelligence_high_risk_import_threshold")
    if "code_intelligence_include_globs" in policy:
        code_intel_flat["include_globs"] = policy.get("code_intelligence_include_globs")
    if "code_intelligence_exclude_globs" in policy:
        code_intel_flat["exclude_globs"] = policy.get("code_intelligence_exclude_globs")
    if "code_intelligence_critical_path_globs" in policy:
        code_intel_flat["critical_path_globs"] = policy.get("code_intelligence_critical_path_globs")
    code_intelligence = normalize_code_intelligence_policy(_deep_merge(code_intel_raw, code_intel_flat))
    return {
        "enabled": enabled,
        "mode": mode,
        "case_sensitive": case_sensitive,
        "allowlist_patterns": allow,
        "denylist_patterns": deny,
        "phases": phases,
        "profile": profile if profile in _PROFILE_PRESETS else "",
        "os_template": os_template,
        "include_os_risky_denylist": include_os_risky,
        "approval_prompt": approval_prompt,
        "approval_non_interactive_decision": approval_non_interactive_decision,
        "approval_auto_allow_patterns": approval_auto_allow_patterns,
        "approval_log_enabled": approval_log_enabled,
        "approval_safe_paths": approval_safe_paths,
        "approval_safe_path_prefixes": approval_safe_path_prefixes,
        "approval_safe_path_globs": approval_safe_path_globs,
        "approval_auto_allow_safe_delete": approval_auto_allow_safe_delete,
        "environment": environment,
        "code_intelligence": code_intelligence,
    }


def _match_any(cmd: str, patterns: List[str], flags: int) -> Tuple[bool, str]:
    for pat in patterns:
        try:
            if re.search(pat, cmd, flags=flags):
                return True, pat
        except re.error:
            # Treat malformed pattern as no-match; policy validation is caller responsibility.
            continue
    return False, ""


def evaluate_command_guardrail(cmd: str, policy: Dict[str, Any], phase: str) -> Dict[str, Any]:
    norm = normalize_guardrail_policy(policy)
    phase = str(phase).strip().lower() or "lane"
    if not norm["enabled"]:
        return {
            "allowed": True,
            "reason": "disabled",
            "mode": norm["mode"],
            "matched_pattern": "",
            "audit_only": False,
            "approval_required": False,
        }
    if phase not in norm["phases"]:
        return {
            "allowed": True,
            "reason": "phase-not-enforced",
            "mode": norm["mode"],
            "matched_pattern": "",
            "audit_only": False,
            "approval_required": False,
        }

    flags = 0 if bool(norm["case_sensitive"]) else re.IGNORECASE
    deny_match, deny_pat = _match_any(cmd, list(norm["denylist_patterns"]), flags=flags)
    if deny_match:
        blocked = norm["mode"] in {"enforce", "human-approval"}
        return {
            "allowed": not blocked,
            "reason": "denylist-match",
            "mode": norm["mode"],
            "matched_pattern": deny_pat,
            "audit_only": not blocked,
            "approval_required": norm["mode"] == "human-approval",
        }

    allowlist = list(norm["allowlist_patterns"])
    if allowlist:
        allow_match, allow_pat = _match_any(cmd, allowlist, flags=flags)
        if not allow_match:
            blocked = norm["mode"] in {"enforce", "human-approval"}
            return {
                "allowed": not blocked,
                "reason": "allowlist-miss",
                "mode": norm["mode"],
                "matched_pattern": "",
                "audit_only": not blocked,
                "approval_required": norm["mode"] == "human-approval",
            }
        return {
            "allowed": True,
            "reason": "allowlist-match",
            "mode": norm["mode"],
            "matched_pattern": allow_pat,
            "audit_only": False,
            "approval_required": False,
        }

    return {
        "allowed": True,
        "reason": "no-rules",
        "mode": norm["mode"],
        "matched_pattern": "",
        "audit_only": False,
        "approval_required": False,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_approval_log(context: Dict[str, Any], row: Dict[str, Any]) -> None:
    project_root = Path(str(context.get("project_root", "")).strip()).resolve() if str(context.get("project_root", "")).strip() else None
    if not project_root:
        pe_root = Path(str(context.get("pe_root", "")).strip()).resolve() if str(context.get("pe_root", "")).strip() else None
        if pe_root:
            project_root = pe_root.parent
    if not project_root:
        return

    run_id = str(context.get("run_id", "")).strip() or "local-run"
    path = project_root / ".plan-executor" / "control" / "guardrail-approvals" / f"{run_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=True) + "\n")


def _interactive_approval_prompt(
    cmd: str,
    phase: str,
    decision: Dict[str, Any],
    context: Dict[str, Any],
) -> bool:
    run_id = str(context.get("run_id", "")).strip() or "local-run"
    lane_id = str(context.get("lane_id", "")).strip() or str(context.get("checkpoint_id", "")).strip() or "unknown"
    reason = str(decision.get("reason", "")).strip()
    pattern = str(decision.get("matched_pattern", "")).strip()
    print("[GUARDRAIL] command requires human approval")
    print(f"  run={run_id} phase={phase} target={lane_id}")
    print(f"  reason={reason} pattern={pattern}")
    print(f"  cmd={cmd}")
    token = input("Approve and execute? [y/N]: ").strip().lower()
    return token in {"y", "yes"}


def _strip_quotes(token: str) -> str:
    out = str(token).strip()
    if len(out) >= 2 and ((out[0] == '"' and out[-1] == '"') or (out[0] == "'" and out[-1] == "'")):
        out = out[1:-1].strip()
    return out


def _to_slash_path(token: str) -> str:
    out = _strip_quotes(token).strip().replace("\\", "/")
    out = re.sub(r"/{2,}", "/", out)
    if out.endswith("/") and out not in {"/", "./", "../"}:
        out = out[:-1]
    return out


def _command_tokens(cmd: str) -> List[str]:
    raw = str(cmd).strip()
    if not raw:
        return []
    for posix in [False, True]:
        try:
            return [str(x).strip() for x in shlex.split(raw, posix=posix) if str(x).strip()]
        except Exception:
            continue
    return [x for x in raw.split() if x.strip()]


def _extract_delete_target(cmd: str) -> Tuple[bool, str]:
    tokens = _command_tokens(cmd)
    if len(tokens) < 2:
        return False, ""
    first = tokens[0].strip().lower()

    if first in {"rm", "/bin/rm"}:
        has_r = any("-r" in t.lower() or "--recursive" in t.lower() for t in tokens[1:])
        has_f = any("-f" in t.lower() or "--force" in t.lower() for t in tokens[1:])
        if not (has_r and has_f):
            return False, ""
        candidates = [t for t in tokens[1:] if not t.startswith("-")]
        return (True, _strip_quotes(candidates[-1])) if candidates else (False, "")

    if first in {"del", "erase"}:
        candidates = [t for t in tokens[1:] if not t.startswith("/")]
        return (True, _strip_quotes(candidates[-1])) if candidates else (False, "")

    if first in {"rd", "rmdir"}:
        candidates = [t for t in tokens[1:] if not t.startswith("/")]
        return (True, _strip_quotes(candidates[-1])) if candidates else (False, "")

    if first in {"remove-item", "remove-item.exe"}:
        candidates = [t for t in tokens[1:] if not t.startswith("-")]
        return (True, _strip_quotes(candidates[0])) if candidates else (False, "")

    return False, ""


def _candidate_paths(path_text: str, context: Dict[str, Any]) -> List[str]:
    token = _to_slash_path(path_text)
    out = []
    if token:
        out.append(token)
    project_root = str(context.get("project_root", "")).strip()
    if project_root and token:
        try:
            root = Path(project_root).resolve()
            p = Path(token)
            resolved = (root / p).resolve() if not p.is_absolute() else p.resolve()
            out.append(_to_slash_path(str(resolved)))
        except Exception:
            pass
    return _dedupe_keep_order(out)


def _path_in_safe_set(path_text: str, norm: Dict[str, Any], context: Dict[str, Any]) -> Tuple[bool, str]:
    paths = _candidate_paths(path_text, context=context)
    safe_paths = [_to_slash_path(x) for x in norm.get("approval_safe_paths", [])]
    safe_prefixes = [_to_slash_path(x) for x in norm.get("approval_safe_path_prefixes", [])]
    safe_globs = [_to_slash_path(x) for x in norm.get("approval_safe_path_globs", [])]

    for p in paths:
        p_lc = p.lower()
        for x in safe_paths:
            if p_lc == x.lower():
                return True, f"safe-path:{x}"
        for x in safe_prefixes:
            x_lc = x.lower()
            if p_lc == x_lc or p_lc.startswith(f"{x_lc}/"):
                return True, f"safe-prefix:{x}"
        for x in safe_globs:
            if fnmatch.fnmatch(p_lc, x.lower()):
                return True, f"safe-glob:{x}"
    return False, ""


def _safe_delete_auto_allow(cmd: str, norm: Dict[str, Any], context: Dict[str, Any]) -> Tuple[bool, str]:
    if not bool(norm.get("approval_auto_allow_safe_delete", True)):
        return False, ""
    is_delete, target = _extract_delete_target(cmd)
    if not is_delete or not target:
        return False, ""
    matched, reason = _path_in_safe_set(target, norm=norm, context=context)
    if not matched:
        return False, ""
    return True, f"safe-delete:{reason}"


def resolve_command_guardrail(
    cmd: str,
    policy: Dict[str, Any],
    phase: str,
    context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    ctx = dict(context or {})
    norm = normalize_guardrail_policy(policy)
    decision = evaluate_command_guardrail(cmd=cmd, policy=norm, phase=phase)
    decision["resolved_role"] = str(norm.get("resolved_role", "")).strip().lower()
    decision["resolved_environment"] = str(norm.get("resolved_environment", "")).strip().lower()
    decision["resolved_sources"] = list(norm.get("resolved_sources", [])) if isinstance(norm.get("resolved_sources", []), list) else []
    if bool(decision.get("allowed", True)) or str(decision.get("mode", "")) != "human-approval":
        return decision
    if not bool(decision.get("approval_required", False)):
        return decision

    flags = 0 if bool(norm.get("case_sensitive", False)) else re.IGNORECASE
    auto_patterns = list(norm.get("approval_auto_allow_patterns", []))
    auto_match, auto_pat = _match_any(cmd, auto_patterns, flags=flags)
    safe_delete_match, safe_delete_reason = _safe_delete_auto_allow(cmd, norm=norm, context=ctx)

    approved = False
    source = ""
    if auto_match:
        approved = True
        source = "auto-allow-pattern"
    elif safe_delete_match:
        approved = True
        source = "safe-delete-path"
    else:
        interactive = bool(sys.stdin.isatty() and sys.stdout.isatty())
        if interactive and bool(norm.get("approval_prompt", True)):
            approved = _interactive_approval_prompt(cmd=cmd, phase=phase, decision=decision, context=ctx)
            source = "prompt"
        else:
            fallback = _normalize_allow_deny_decision(norm.get("approval_non_interactive_decision", "deny"), default="deny")
            approved = fallback == "allow"
            source = "non-interactive-default"

    out = dict(decision)
    if approved:
        out["allowed"] = True
        out["audit_only"] = True
        out["reason"] = "human-approved"
    else:
        out["allowed"] = False
        out["audit_only"] = False
        out["reason"] = "human-denied"
    out["approval_required"] = True
    out["approval_decision"] = "approved" if approved else "denied"
    out["approval_source"] = source
    if auto_match:
        out["approval_matched_pattern"] = auto_pat
    if safe_delete_match:
        out["approval_safe_delete_reason"] = safe_delete_reason
    out["original_reason"] = str(decision.get("reason", ""))
    out["original_matched_pattern"] = str(decision.get("matched_pattern", ""))

    if bool(norm.get("approval_log_enabled", True)):
        try:
            _append_approval_log(
                context=ctx,
                row={
                    "ts": _utc_now(),
                    "run_id": str(ctx.get("run_id", "")).strip(),
                    "lane_id": str(ctx.get("lane_id", "")).strip(),
                    "checkpoint_id": str(ctx.get("checkpoint_id", "")).strip(),
                    "phase": str(phase).strip().lower(),
                    "cmd": cmd,
                    "decision": out.get("approval_decision", ""),
                    "source": out.get("approval_source", ""),
                    "reason": out.get("original_reason", out.get("reason", "")),
                    "matched_pattern": out.get("original_matched_pattern", out.get("matched_pattern", "")),
                },
            )
        except Exception:
            pass
    return out
