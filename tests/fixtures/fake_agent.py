"""Portable fake provider for subprocess contracts; never accesses the network.

Record mode writes only explicitly selected AI_SKILLS_TEST_* environment values.
Partial emits bytes without a newline before sleeping; descendant creates a child
in the same process tree, leaving cleanup to the supervisor under test.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("record", "partial", "quiet", "failure", "artifact", "descendant", "ignore-termination"), default="record")
    parser.add_argument("--record", type=Path)
    parser.add_argument("--seconds", type=float, default=1)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--exit-code", type=int, default=1)
    args, _ = parser.parse_known_args()
    if args.record:
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.write_text(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(),
            "pid": os.getpid(), "environment": {key: value for key, value in os.environ.items()
            if key.startswith("AI_SKILLS_TEST_")}}, ensure_ascii=True), encoding="utf-8")
    if args.mode == "failure":
        print(json.dumps({"status": "failed", "reason": "fake-provider-failure"}), file=sys.stderr)
        return args.exit_code
    if args.mode == "artifact":
        if args.artifact is None:
            parser.error("artifact mode requires --artifact")
        args.artifact.parent.mkdir(parents=True, exist_ok=True)
        args.artifact.write_text(json.dumps({"schema_version": 1, "status": "success"}), encoding="utf-8")
    elif args.mode == "partial":
        sys.stdout.buffer.write(b"partial output")
        sys.stdout.buffer.flush()
        time.sleep(args.seconds)
    elif args.mode == "quiet":
        time.sleep(args.seconds)
    elif args.mode == "ignore-termination":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(args.seconds)
    elif args.mode == "descendant":
        child = subprocess.Popen([sys.executable, __file__, "--mode", "quiet", "--seconds", str(args.seconds)])
        print(json.dumps({"pid": os.getpid(), "child_pid": child.pid}), flush=True)
        child.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
