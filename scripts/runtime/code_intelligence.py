#!/usr/bin/env python3
"""Code-change intelligence for lane execution impact analysis."""

from __future__ import annotations

import ast
import fnmatch
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple


_CODE_EXT_LANG: Dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "typescript",
    ".jsx": "typescript",
}

_DEFAULT_EXCLUDE_GLOBS = [
    ".git/**",
    ".plan-executor/**",
    "node_modules/**",
    "dist/**",
    "build/**",
    "coverage/**",
]

_DEFAULT_CRITICAL_PATH_GLOBS = [
    "scripts/runtime/orchestrator.py",
    "scripts/runtime/worker_adapters.py",
    "scripts/runtime/gate_engine.py",
    "scripts/runtime/command_guardrails.py",
    "scripts/runtime/runbook_lint.py",
]


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


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
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
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _to_slash_path(path: str) -> str:
    out = str(path).strip().strip('"').strip("'").replace("\\", "/")
    out = re.sub(r"/{2,}", "/", out)
    if out.startswith("./"):
        out = out[2:]
    while out.startswith("/"):
        out = out[1:]
    return out


def _normalize_mode(value: Any) -> str:
    token = str(value).strip().lower()
    if token in {"enforce", "audit"}:
        return token
    return "audit"


def normalize_code_intelligence_policy(raw_policy: Any) -> Dict[str, Any]:
    policy = dict(raw_policy) if isinstance(raw_policy, dict) else {}
    enabled = _safe_bool(policy.get("enabled", False), False)
    mode = _normalize_mode(policy.get("mode", "audit"))
    max_total_code_files = max(1, _safe_int(policy.get("max_total_code_files", 25), 25))
    max_high_risk_files = max(0, _safe_int(policy.get("max_high_risk_files", 3), 3))
    high_risk_symbol_threshold = max(1, _safe_int(policy.get("high_risk_symbol_threshold", 25), 25))
    high_risk_import_threshold = max(1, _safe_int(policy.get("high_risk_import_threshold", 20), 20))
    max_findings = max(1, _safe_int(policy.get("max_findings", 20), 20))
    languages = [x.lower() for x in _to_list(policy.get("languages", ["python", "typescript"]))]
    languages = [x for x in languages if x in {"python", "typescript"}]
    if not languages:
        languages = ["python", "typescript"]

    include_globs = _dedupe_keep_order(_to_list(policy.get("include_globs", [])))
    exclude_globs = _dedupe_keep_order(_to_list(policy.get("exclude_globs", _DEFAULT_EXCLUDE_GLOBS)))
    critical_path_globs = _dedupe_keep_order(
        _to_list(policy.get("critical_path_globs", _DEFAULT_CRITICAL_PATH_GLOBS))
    )

    return {
        "enabled": enabled,
        "mode": mode,
        "max_total_code_files": max_total_code_files,
        "max_high_risk_files": max_high_risk_files,
        "high_risk_symbol_threshold": high_risk_symbol_threshold,
        "high_risk_import_threshold": high_risk_import_threshold,
        "max_findings": max_findings,
        "languages": languages,
        "include_globs": include_globs,
        "exclude_globs": exclude_globs,
        "critical_path_globs": critical_path_globs,
    }


def snapshot_git_changed_files(project_root: Path) -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            "git status --porcelain --untracked-files=all",
            shell=True,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception as exc:
        return {
            "available": False,
            "files": [],
            "error": str(exc),
        }
    if proc.returncode != 0:
        return {
            "available": False,
            "files": [],
            "error": (proc.stderr or proc.stdout or "git status failed")[-300:],
        }
    out: List[str] = []
    for raw_line in (proc.stdout or "").splitlines():
        line = str(raw_line)
        if not line.strip():
            continue
        path_part = line[3:].strip() if len(line) >= 4 else line.strip()
        if " -> " in path_part:
            path_part = path_part.split(" -> ", 1)[1].strip()
        path_norm = _to_slash_path(path_part)
        if path_norm:
            out.append(path_norm)
    return {
        "available": True,
        "files": sorted(set(out)),
        "error": "",
    }


def _match_any_glob(path: str, globs: List[str]) -> Tuple[bool, str]:
    p = _to_slash_path(path)
    p_lc = p.lower()
    for raw in globs:
        g = _to_slash_path(raw)
        if not g:
            continue
        if fnmatch.fnmatch(p_lc, g.lower()):
            return True, raw
    return False, ""


def _is_code_file(path: str, languages: List[str]) -> Tuple[bool, str]:
    ext = Path(path).suffix.lower()
    lang = _CODE_EXT_LANG.get(ext, "")
    if not lang:
        return False, ""
    if lang not in languages:
        return False, ""
    return True, lang


def _parse_python_source(text: str) -> Dict[str, Any]:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return {
            "parse_ok": False,
            "parse_error": f"{exc.msg} (line {exc.lineno})",
            "symbol_names": [],
            "symbol_count": 0,
            "import_count": 0,
            "dynamic_exec": False,
        }

    symbols: List[str] = []
    import_count = 0
    dynamic_exec = False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append(str(node.name))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            import_count += 1
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and str(node.func.id) in {"eval", "exec", "compile"}:
                dynamic_exec = True
            if isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and str(node.func.value.id) == "subprocess":
                    if str(node.func.attr) in {"run", "Popen", "call"}:
                        for kw in node.keywords:
                            if str(kw.arg) == "shell" and isinstance(kw.value, ast.Constant) and bool(kw.value.value):
                                dynamic_exec = True
    return {
        "parse_ok": True,
        "parse_error": "",
        "symbol_names": symbols[:30],
        "symbol_count": len(symbols),
        "import_count": import_count,
        "dynamic_exec": dynamic_exec,
    }


def _parse_typescript_source(text: str) -> Dict[str, Any]:
    export_patterns = [
        r"^\s*export\s+(?:async\s+)?function\s+([A-Za-z_]\w*)",
        r"^\s*export\s+class\s+([A-Za-z_]\w*)",
        r"^\s*export\s+interface\s+([A-Za-z_]\w*)",
        r"^\s*export\s+type\s+([A-Za-z_]\w*)",
        r"^\s*export\s+(?:const|let|var)\s+([A-Za-z_]\w*)",
        r"^\s*export\s+enum\s+([A-Za-z_]\w*)",
    ]
    symbols: List[str] = []
    for pat in export_patterns:
        for m in re.finditer(pat, text, flags=re.MULTILINE):
            symbols.append(str(m.group(1)))
    if re.search(r"^\s*export\s+default\b", text, flags=re.MULTILINE):
        symbols.append("default")
    import_count = len(re.findall(r"^\s*import\b", text, flags=re.MULTILINE))
    dynamic_exec = bool(
        re.search(r"\beval\s*\(", text)
        or re.search(r"\bnew\s+Function\s*\(", text)
        or re.search(r"\bchild_process\.(?:exec|spawn)\s*\(", text)
    )
    return {
        "parse_ok": True,
        "parse_error": "",
        "symbol_names": symbols[:30],
        "symbol_count": len(symbols),
        "import_count": import_count,
        "dynamic_exec": dynamic_exec,
    }


def _score_risk(path: str, parsed: Dict[str, Any], policy: Dict[str, Any]) -> Tuple[str, List[str]]:
    reasons: List[str] = []
    risk = "low"

    critical_match, critical_pat = _match_any_glob(path, list(policy.get("critical_path_globs", [])))
    if critical_match:
        risk = "high"
        reasons.append(f"critical-path:{critical_pat}")

    parse_ok = bool(parsed.get("parse_ok", False))
    if not parse_ok:
        if risk != "high":
            risk = "medium"
        reasons.append("parse-error")

    symbol_count = int(parsed.get("symbol_count", 0))
    import_count = int(parsed.get("import_count", 0))
    high_symbol = int(policy.get("high_risk_symbol_threshold", 25))
    high_import = int(policy.get("high_risk_import_threshold", 20))

    if symbol_count >= high_symbol:
        risk = "high"
        reasons.append(f"symbol-count>={high_symbol}")
    elif symbol_count >= max(3, high_symbol // 2):
        if risk == "low":
            risk = "medium"
        reasons.append("symbol-count-medium")

    if import_count >= high_import:
        risk = "high"
        reasons.append(f"import-count>={high_import}")
    elif import_count >= max(5, high_import // 2):
        if risk == "low":
            risk = "medium"
        reasons.append("import-count-medium")

    if bool(parsed.get("dynamic_exec", False)):
        risk = "high"
        reasons.append("dynamic-exec")

    if not reasons:
        reasons.append("low-risk-signals")
    return risk, reasons[:8]


def analyze_code_change_impact(
    project_root: Path,
    baseline_changed_files: Set[str],
    policy: Dict[str, Any],
) -> Dict[str, Any]:
    norm = normalize_code_intelligence_policy(policy)
    if not bool(norm.get("enabled", False)):
        return {
            "enabled": False,
            "applied": False,
            "mode": str(norm.get("mode", "audit")),
            "violation": False,
            "should_block": False,
            "summary": "code-intelligence disabled",
            "touched_files": [],
            "code_files": [],
            "findings": [],
            "high_risk_file_count": 0,
            "code_file_count": 0,
            "violation_reasons": [],
        }

    snap = snapshot_git_changed_files(project_root)
    if not bool(snap.get("available", False)):
        return {
            "enabled": True,
            "applied": False,
            "mode": str(norm.get("mode", "audit")),
            "violation": False,
            "should_block": False,
            "summary": f"code-intelligence skipped: {str(snap.get('error', 'git unavailable')).strip()}",
            "touched_files": [],
            "code_files": [],
            "findings": [],
            "high_risk_file_count": 0,
            "code_file_count": 0,
            "violation_reasons": [],
        }

    before = {_to_slash_path(x) for x in baseline_changed_files}
    after = {_to_slash_path(x) for x in snap.get("files", [])}
    touched = sorted([x for x in (after - before) if x])

    include_globs = list(norm.get("include_globs", []))
    exclude_globs = list(norm.get("exclude_globs", []))

    code_candidates: List[Tuple[str, str]] = []
    for rel in touched:
        if include_globs:
            include_hit, _ = _match_any_glob(rel, include_globs)
            if not include_hit:
                continue
        exclude_hit, _ = _match_any_glob(rel, exclude_globs)
        if exclude_hit:
            continue
        is_code, lang = _is_code_file(rel, list(norm.get("languages", [])))
        if is_code:
            code_candidates.append((rel, lang))

    findings: List[Dict[str, Any]] = []
    for rel, lang in code_candidates:
        file_path = (project_root / rel).resolve()
        exists = file_path.exists()
        text = ""
        read_error = ""
        if exists:
            try:
                text = file_path.read_text(encoding="utf-8")
            except Exception as exc:
                read_error = str(exc)
        if lang == "python":
            parsed = _parse_python_source(text) if exists and not read_error else {
                "parse_ok": False,
                "parse_error": read_error or "missing-file",
                "symbol_names": [],
                "symbol_count": 0,
                "import_count": 0,
                "dynamic_exec": False,
            }
        else:
            parsed = _parse_typescript_source(text) if exists and not read_error else {
                "parse_ok": False,
                "parse_error": read_error or "missing-file",
                "symbol_names": [],
                "symbol_count": 0,
                "import_count": 0,
                "dynamic_exec": False,
            }
        risk, reasons = _score_risk(rel, parsed=parsed, policy=norm)
        findings.append(
            {
                "path": rel,
                "language": lang,
                "risk": risk,
                "reasons": reasons,
                "parse_ok": bool(parsed.get("parse_ok", False)),
                "symbol_count": int(parsed.get("symbol_count", 0)),
                "import_count": int(parsed.get("import_count", 0)),
                "dynamic_exec": bool(parsed.get("dynamic_exec", False)),
                "symbols_preview": list(parsed.get("symbol_names", []))[:10],
            }
        )

    high_risk_count = len([x for x in findings if str(x.get("risk", "")) == "high"])
    code_file_count = len(findings)
    violation_reasons: List[str] = []
    if code_file_count > int(norm.get("max_total_code_files", 25)):
        violation_reasons.append(
            f"code-files>{int(norm.get('max_total_code_files', 25))}"
        )
    if high_risk_count > int(norm.get("max_high_risk_files", 3)):
        violation_reasons.append(
            f"high-risk-files>{int(norm.get('max_high_risk_files', 3))}"
        )

    violation = bool(violation_reasons)
    should_block = violation and str(norm.get("mode", "audit")) == "enforce"
    summary = (
        f"code-intel touched={len(touched)} code={code_file_count} "
        f"high={high_risk_count} mode={str(norm.get('mode', 'audit'))}"
    )
    if violation_reasons:
        summary += f" violation={','.join(violation_reasons)}"

    max_findings = int(norm.get("max_findings", 20))
    return {
        "enabled": True,
        "applied": True,
        "mode": str(norm.get("mode", "audit")),
        "violation": violation,
        "should_block": should_block,
        "summary": summary,
        "touched_files": touched[: max(10, max_findings)],
        "code_files": [x["path"] for x in findings][: max(10, max_findings)],
        "findings": findings[:max_findings],
        "high_risk_file_count": high_risk_count,
        "code_file_count": code_file_count,
        "violation_reasons": violation_reasons,
    }

