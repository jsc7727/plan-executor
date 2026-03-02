#!/usr/bin/env python3
"""Runbook lint utilities for plan-executor runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .command_guardrails import known_guardrail_profiles, normalize_guardrail_policy


def _issue(level: str, code: str, message: str, path: str) -> Dict[str, str]:
    return {
        "level": str(level).strip().lower(),
        "code": str(code).strip().lower(),
        "message": str(message).strip(),
        "path": str(path).strip(),
    }


def lint_runbook_payload(runbook: Any, strict: bool = True) -> Dict[str, Any]:
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    if not isinstance(runbook, dict):
        errors.append(_issue("error", "runbook-type", "Runbook must be a JSON object.", "$"))
        return {
            "ok": False,
            "errors": errors,
            "warnings": warnings,
            "normalized_command_guardrails": {},
            "error_count": len(errors),
            "warning_count": len(warnings),
        }

    limits = runbook.get("limits", {})
    if not isinstance(limits, dict):
        errors.append(_issue("error", "limits-type", "`limits` must be an object.", "limits"))
        limits = {}

    raw_guardrails = limits.get("command_guardrails", None)
    normalized_guardrails: Dict[str, Any] = {}

    if not isinstance(raw_guardrails, dict):
        errors.append(
            _issue(
                "error",
                "guardrail-required",
                "Missing required `limits.command_guardrails` policy.",
                "limits.command_guardrails",
            )
        )
    else:
        profile = str(raw_guardrails.get("profile", "")).strip().lower()
        if profile and profile not in known_guardrail_profiles():
            errors.append(
                _issue(
                    "error",
                    "guardrail-profile",
                    f"Unknown command_guardrails profile: '{profile}'.",
                    "limits.command_guardrails.profile",
                )
            )
        role_policies = raw_guardrails.get("role_policies", {})
        if role_policies and not isinstance(role_policies, dict):
            errors.append(
                _issue(
                    "error",
                    "guardrail-role-policies-type",
                    "`role_policies` must be an object keyed by role.",
                    "limits.command_guardrails.role_policies",
                )
            )
            role_policies = {}
        for raw_role, row in (role_policies.items() if isinstance(role_policies, dict) else []):
            role = str(raw_role).strip().lower()
            if not role:
                errors.append(
                    _issue(
                        "error",
                        "guardrail-role-policy-key",
                        "role_policies contains an empty role key.",
                        "limits.command_guardrails.role_policies",
                    )
                )
                continue
            if not isinstance(row, dict):
                errors.append(
                    _issue(
                        "error",
                        "guardrail-role-policy-row",
                        f"role_policies['{role}'] must be an object.",
                        f"limits.command_guardrails.role_policies.{role}",
                    )
                )
                continue
            mode = str(row.get("mode", "")).strip().lower()
            if mode and mode not in {"enforce", "audit", "human-approval"}:
                errors.append(
                    _issue(
                        "error",
                        "guardrail-role-policy-mode",
                        f"role_policies['{role}'].mode must be one of enforce|audit|human-approval.",
                        f"limits.command_guardrails.role_policies.{role}.mode",
                    )
                )

        env_policies = raw_guardrails.get("environment_policies", {})
        if env_policies and not isinstance(env_policies, dict):
            errors.append(
                _issue(
                    "error",
                    "guardrail-env-policies-type",
                    "`environment_policies` must be an object keyed by environment.",
                    "limits.command_guardrails.environment_policies",
                )
            )
            env_policies = {}
        for raw_env, row in (env_policies.items() if isinstance(env_policies, dict) else []):
            env = str(raw_env).strip().lower()
            if not env:
                errors.append(
                    _issue(
                        "error",
                        "guardrail-env-policy-key",
                        "environment_policies contains an empty environment key.",
                        "limits.command_guardrails.environment_policies",
                    )
                )
                continue
            if not isinstance(row, dict):
                errors.append(
                    _issue(
                        "error",
                        "guardrail-env-policy-row",
                        f"environment_policies['{env}'] must be an object.",
                        f"limits.command_guardrails.environment_policies.{env}",
                    )
                )
                continue
            mode = str(row.get("mode", "")).strip().lower()
            if mode and mode not in {"enforce", "audit", "human-approval"}:
                errors.append(
                    _issue(
                        "error",
                        "guardrail-env-policy-mode",
                        f"environment_policies['{env}'].mode must be one of enforce|audit|human-approval.",
                        f"limits.command_guardrails.environment_policies.{env}.mode",
                    )
                )

        code_intel = raw_guardrails.get("code_intelligence", {})
        if code_intel and not isinstance(code_intel, dict):
            errors.append(
                _issue(
                    "error",
                    "guardrail-code-intel-type",
                    "`code_intelligence` must be an object.",
                    "limits.command_guardrails.code_intelligence",
                )
            )
            code_intel = {}
        if isinstance(code_intel, dict) and code_intel:
            ci_mode = str(code_intel.get("mode", "")).strip().lower()
            if ci_mode and ci_mode not in {"enforce", "audit"}:
                errors.append(
                    _issue(
                        "error",
                        "guardrail-code-intel-mode",
                        "`code_intelligence.mode` must be one of enforce|audit.",
                        "limits.command_guardrails.code_intelligence.mode",
                    )
                )
            for key, minimum in [
                ("max_total_code_files", 1),
                ("max_high_risk_files", 0),
                ("high_risk_symbol_threshold", 1),
                ("high_risk_import_threshold", 1),
            ]:
                if key not in code_intel:
                    continue
                try:
                    value = int(code_intel.get(key))
                except Exception:
                    errors.append(
                        _issue(
                            "error",
                            "guardrail-code-intel-value",
                            f"`code_intelligence.{key}` must be an integer.",
                            f"limits.command_guardrails.code_intelligence.{key}",
                        )
                    )
                    continue
                if value < minimum:
                    errors.append(
                        _issue(
                            "error",
                            "guardrail-code-intel-range",
                            f"`code_intelligence.{key}` must be >= {minimum}.",
                            f"limits.command_guardrails.code_intelligence.{key}",
                        )
                    )

        normalized_guardrails = normalize_guardrail_policy(raw_guardrails)
        if not bool(normalized_guardrails.get("enabled", False)):
            errors.append(
                _issue(
                    "error",
                    "guardrail-disabled",
                    "`limits.command_guardrails.enabled` must be true for runtime lint strict mode.",
                    "limits.command_guardrails.enabled",
                )
            )

        allow = list(normalized_guardrails.get("allowlist_patterns", []))
        deny = list(normalized_guardrails.get("denylist_patterns", []))
        if not allow and not deny:
            errors.append(
                _issue(
                    "error",
                    "guardrail-empty",
                    "command_guardrails must define allowlist or denylist patterns.",
                    "limits.command_guardrails",
                )
            )

        phases = {str(x).strip().lower() for x in normalized_guardrails.get("phases", []) if str(x).strip()}
        if strict and "lane" not in phases:
            errors.append(
                _issue(
                    "error",
                    "guardrail-phase-lane",
                    "Strict lint requires lane phase coverage in command_guardrails.phases.",
                    "limits.command_guardrails.phases",
                )
            )
        if strict and "gate" not in phases:
            warnings.append(
                _issue(
                    "warning",
                    "guardrail-phase-gate",
                    "Gate phase is not covered; gate_commands will run without guardrail checks.",
                    "limits.command_guardrails.phases",
                )
            )

        if strict and str(normalized_guardrails.get("mode", "enforce")) == "audit":
            warnings.append(
                _issue(
                    "warning",
                    "guardrail-audit-mode",
                    "Audit mode does not block commands; consider enforce mode for CI/prod.",
                    "limits.command_guardrails.mode",
                )
            )
        if strict and str(normalized_guardrails.get("mode", "enforce")) == "human-approval":
            warnings.append(
                _issue(
                    "warning",
                    "guardrail-human-approval-mode",
                    "human-approval mode requires an interactive operator; use enforce mode for daemon/CI/prod.",
                    "limits.command_guardrails.mode",
                )
            )

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "normalized_command_guardrails": normalized_guardrails,
        "error_count": len(errors),
        "warning_count": len(warnings),
    }


def lint_runbook_file(path: Path, strict: bool = True) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    out = lint_runbook_payload(payload, strict=strict)
    out["runbook"] = str(path.resolve())
    return out
