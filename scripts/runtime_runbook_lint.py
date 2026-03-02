#!/usr/bin/env python3
"""CLI for runbook lint checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime.runbook_lint import lint_runbook_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="plan-executor runbook lint CLI")
    parser.add_argument("--runbook", required=True, help="Path to runbook JSON.")
    parser.add_argument(
        "--strict",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Strict lint mode (default: true).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runbook = Path(args.runbook).resolve()
    if not runbook.exists():
        print(f"[ERROR] missing file: {runbook}")
        return 2

    result = lint_runbook_file(runbook, strict=bool(args.strict))
    ok = bool(result.get("ok", False))

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("Runbook Lint")
        print("=" * 32)
        print(f"runbook={result.get('runbook', str(runbook))}")
        print(
            f"ok={ok} errors={result.get('error_count', 0)} warnings={result.get('warning_count', 0)} "
            f"strict={bool(args.strict)}"
        )
        for row in result.get("errors", []):
            print(f"[ERROR] {row.get('code')} path={row.get('path')} message={row.get('message')}")
        for row in result.get("warnings", []):
            print(f"[WARN ] {row.get('code')} path={row.get('path')} message={row.get('message')}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

