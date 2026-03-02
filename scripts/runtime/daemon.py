#!/usr/bin/env python3
"""Job queue daemon for plan-executor runtime."""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .orchestrator import RuntimeOrchestrator
from .runbook_lint import lint_runbook_file


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RuntimeDaemon:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.pe_root = self.project_root / ".plan-executor"
        self.pending = self.pe_root / "queue" / "pending"
        self.processing = self.pe_root / "queue" / "processing"
        self.done = self.pe_root / "queue" / "done"
        self.failed = self.pe_root / "queue" / "failed"
        self.pid_file = self.pe_root / "daemon.pid"
        for d in [self.pending, self.processing, self.done, self.failed]:
            d.mkdir(parents=True, exist_ok=True)
        self.orchestrator = RuntimeOrchestrator(self.project_root)

    @staticmethod
    def _lint_error_text(lint: Dict[str, object]) -> str:
        rows = lint.get("errors", [])
        if isinstance(rows, list) and rows:
            parts = []
            for row in rows[:3]:
                if not isinstance(row, dict):
                    continue
                code = str(row.get("code", "lint-error")).strip()
                path = str(row.get("path", "")).strip()
                msg = str(row.get("message", "")).strip()
                parts.append(f"{code}({path}): {msg}")
            return "; ".join(parts) if parts else "runbook lint failed"
        return "runbook lint failed"

    def enqueue(
        self,
        runbook: Path,
        manifest: Path | None,
        adapter: str,
        run_id: str | None = None,
        skip_runbook_lint: bool = False,
    ) -> Path:
        lint_result: Dict[str, object] = {}
        if not skip_runbook_lint:
            lint_result = lint_runbook_file(runbook, strict=True)
            if not bool(lint_result.get("ok", False)):
                raise ValueError(f"runbook lint failed: {self._lint_error_text(lint_result)}")

        job_id = f"job-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
        payload = {
            "job_id": job_id,
            "created_at": utc_now(),
            "runbook": str(runbook.resolve()),
            "manifest": str(manifest.resolve()) if manifest else "",
            "adapter": adapter or "auto",
            "run_id": run_id or "",
            "lint_checked": not skip_runbook_lint,
            "lint_ok": bool(lint_result.get("ok", True)) if lint_result else bool(skip_runbook_lint),
            "lint_error_count": int(lint_result.get("error_count", 0)) if lint_result else 0,
            "lint_warning_count": int(lint_result.get("warning_count", 0)) if lint_result else 0,
        }
        path = self.pending / f"{job_id}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def _load_job(self, path: Path) -> Dict[str, str]:
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def _claim_next_job(self) -> Path | None:
        jobs = sorted(self.pending.glob("*.json"))
        if not jobs:
            return None
        src = jobs[0]
        dst = self.processing / src.name
        src.replace(dst)
        return dst

    def recover_stale_processing(self, stale_sec: int = 120, max_retries: int = 2) -> Dict[str, int]:
        now = time.time()
        recovered = 0
        failed = 0
        for path in sorted(self.processing.glob("*.json")):
            age_sec = now - path.stat().st_mtime
            if age_sec < stale_sec:
                continue
            job = self._load_job(path)
            retry_count = int(job.get("retry_count", 0)) + 1
            job["retry_count"] = retry_count
            job["recovered_at"] = utc_now()
            job["recover_reason"] = f"stale-processing>{stale_sec}s"
            if retry_count > max_retries:
                job["status"] = "failed"
                job["error"] = "exceeded max recovery retries"
                fail_path = self.failed / path.name
                fail_path.write_text(json.dumps(job, indent=2), encoding="utf-8")
                path.unlink(missing_ok=True)
                failed += 1
            else:
                job["status"] = "pending"
                pending_path = self.pending / path.name
                pending_path.write_text(json.dumps(job, indent=2), encoding="utf-8")
                path.unlink(missing_ok=True)
                recovered += 1
        return {"recovered": recovered, "failed": failed}

    def run_once(self, max_jobs: int = 1) -> Dict[str, int]:
        recovery = self.recover_stale_processing(stale_sec=60, max_retries=2)
        processed = 0
        succeeded = 0
        failed = 0
        while processed < max_jobs:
            job_path = self._claim_next_job()
            if not job_path:
                break
            processed += 1
            try:
                job = self._load_job(job_path)
                runbook = Path(job["runbook"])
                manifest = Path(job["manifest"]) if job.get("manifest") else None
                lint = lint_runbook_file(runbook, strict=True)
                if not bool(lint.get("ok", False)):
                    raise ValueError(f"runbook lint failed: {self._lint_error_text(lint)}")
                self.orchestrator.start(
                    runbook_path=runbook,
                    manifest_path=manifest,
                    adapter_name=job.get("adapter", "auto"),
                    run_id=job.get("run_id") or None,
                )
                job["processed_at"] = utc_now()
                job["status"] = "done"
                done_path = self.done / job_path.name
                done_path.write_text(json.dumps(job, indent=2), encoding="utf-8")
                job_path.unlink(missing_ok=True)
                succeeded += 1
            except Exception as exc:
                job = self._load_job(job_path)
                job["processed_at"] = utc_now()
                job["status"] = "failed"
                job["error"] = str(exc)
                fail_path = self.failed / job_path.name
                fail_path.write_text(json.dumps(job, indent=2), encoding="utf-8")
                job_path.unlink(missing_ok=True)
                failed += 1
        return {
            "processed": processed,
            "succeeded": succeeded,
            "failed": failed,
            "recovered": recovery["recovered"],
            "recovery_failed": recovery["failed"],
        }

    def serve(self, interval_sec: float = 1.0, max_jobs_per_tick: int = 1) -> None:
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.pid_file.write_text(str(os.getpid()), encoding="utf-8")
        try:
            while True:
                self.run_once(max_jobs=max_jobs_per_tick)
                time.sleep(interval_sec)
        finally:
            self.pid_file.unlink(missing_ok=True)

    def queue_stats(self) -> Dict[str, int]:
        return {
            "pending": len(list(self.pending.glob("*.json"))),
            "processing": len(list(self.processing.glob("*.json"))),
            "done": len(list(self.done.glob("*.json"))),
            "failed": len(list(self.failed.glob("*.json"))),
        }
