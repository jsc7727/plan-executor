#!/usr/bin/env python3
"""CLI for runtime control plane (IPC + file double logging)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime.control_plane import (
    append_control_message,
    count_control_messages,
    read_control_messages,
    send_control_ipc,
    serve_control_ipc,
)


def parse_payload(json_text: str, json_file: str) -> dict:
    if json_file:
        path = Path(json_file).resolve()
        return json.loads(path.read_text(encoding="utf-8-sig"))
    if json_text.strip():
        return json.loads(json_text)
    return {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="plan-executor runtime control CLI")
    parser.add_argument("--project-root", default=".", help="Project root containing .plan-executor")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Serve IPC control server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    send = sub.add_parser("send", help="Send control message over IPC")
    send.add_argument("--host", default="127.0.0.1")
    send.add_argument("--port", type=int, default=8765)
    send.add_argument("--run-id", required=True)
    send.add_argument("--kind", default="replan")
    send.add_argument("--payload-json", default="")
    send.add_argument("--payload-file", default="")

    cp = sub.add_parser("consensus-patch", help="Patch checkpoint consensus_gate policy (IPC/file)")
    cp.add_argument("--run-id", required=True)
    cp.add_argument("--checkpoint-id", default="", help="Single checkpoint id target")
    cp.add_argument("--checkpoint-ids", default="", help="Comma-separated checkpoint ids")
    cp.add_argument("--patch-json", default="", help="JSON object for consensus_gate_patch")
    cp.add_argument("--patch-file", default="", help="Path to JSON file for consensus_gate_patch")
    cp.add_argument("--replace", action="store_true", help="Replace consensus_gate instead of deep merge")
    cp.add_argument("--reason", default="mid-run-consensus-reconfigure")
    cp.add_argument("--transport", choices=["ipc", "file"], default="ipc")
    cp.add_argument("--host", default="127.0.0.1")
    cp.add_argument("--port", type=int, default=8765)

    enqueue = sub.add_parser("enqueue", help="Append control message directly to file queue")
    enqueue.add_argument("--run-id", required=True)
    enqueue.add_argument("--kind", default="replan")
    enqueue.add_argument("--payload-json", default="")
    enqueue.add_argument("--payload-file", default="")

    stat = sub.add_parser("stats", help="Count control messages for run")
    stat.add_argument("--run-id", required=True)

    ls = sub.add_parser("list", help="List control messages for run")
    ls.add_argument("--run-id", required=True)
    ls.add_argument("--offset", type=int, default=0)
    ls.add_argument("--limit", type=int, default=50)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    try:
        if args.command == "serve":
            print(f"[OK] control IPC serve host={args.host} port={args.port}")
            serve_control_ipc(project_root=project_root, host=args.host, port=args.port)
            return 0

        if args.command == "send":
            payload = parse_payload(args.payload_json, args.payload_file)
            resp = send_control_ipc(
                host=args.host,
                port=args.port,
                message={
                    "run_id": args.run_id,
                    "kind": args.kind,
                    "payload": payload,
                },
            )
            print(json.dumps(resp, indent=2))
            return 0

        if args.command == "consensus-patch":
            patch = parse_payload(args.patch_json, args.patch_file)
            if not isinstance(patch, dict):
                raise ValueError("consensus-patch requires patch JSON object")
            checkpoint_ids = [x.strip() for x in str(args.checkpoint_ids).split(",") if x.strip()]
            payload = {
                "reason": str(args.reason).strip(),
                "checkpoint_id": str(args.checkpoint_id).strip(),
                "checkpoint_ids": checkpoint_ids,
                "consensus_gate_patch": patch,
                "replace": bool(args.replace),
            }
            message = {
                "run_id": args.run_id,
                "kind": "consensus_reconfigure",
                "payload": payload,
            }
            if args.transport == "file":
                row = append_control_message(
                    project_root=project_root,
                    message=message,
                    source="file",
                )
                print(json.dumps(row, indent=2))
                return 0
            resp = send_control_ipc(
                host=args.host,
                port=args.port,
                message=message,
            )
            print(json.dumps(resp, indent=2))
            return 0

        if args.command == "enqueue":
            payload = parse_payload(args.payload_json, args.payload_file)
            row = append_control_message(
                project_root=project_root,
                message={
                    "run_id": args.run_id,
                    "kind": args.kind,
                    "payload": payload,
                },
                source="file",
            )
            print(json.dumps(row, indent=2))
            return 0

        if args.command == "stats":
            count = count_control_messages(project_root, args.run_id)
            print(f"run_id={args.run_id} control_messages={count}")
            return 0

        if args.command == "list":
            rows, new_offset = read_control_messages(project_root, args.run_id, offset=max(0, args.offset), limit=max(1, args.limit))
            print(json.dumps({"rows": rows, "new_offset": new_offset}, indent=2))
            return 0

        print("[ERROR] unsupported command")
        return 1
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
