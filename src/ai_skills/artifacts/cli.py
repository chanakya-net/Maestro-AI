"""CLI entrypoint for ``ai-skills artifact``."""
from __future__ import annotations

import argparse
import sys

from ai_skills.artifacts.schemas import failure_class
from ai_skills.artifacts.synthesis import failure_reason, synthesize, write_json

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("failure-reason", "failure-class", "synthesize"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--role", required=True)
        sub.add_argument("--issue", required=True)
        sub.add_argument("--result-file", required=True)
        sub.add_argument("--done-file", default="")
        sub.add_argument("--log-file", default="")
        sub.add_argument("--issue-dir", default="")
        sub.add_argument("--repo-root", default="")
        sub.add_argument("--pre-spawn-head", default="")
        sub.add_argument("--from-stall", action="store_true", default=False)
    subparsers.add_parser("check-scope", add_help=False,
        help="Print which required checks a modify change may skip under the repository check-scopes map.")
    writer = subparsers.add_parser("write-json")
    writer.add_argument(
        "--role",
        required=True,
        choices=[
            "impl",
            "modify",
            "complexity",
            "plan",
            "review",
            "review-instructions",
            "artifact-recovery",
            "merge-recovery",
            "sub-coord",
        ],
    )
    writer.add_argument("--issue", required=True)
    writer.add_argument("--payload-file", required=True)
    writer.add_argument("--result-file", required=True)
    writer.add_argument("--issue-dir", default="")
    writer.add_argument("--repo-root", default="")
    writer.add_argument("--pre-spawn-head", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["check-scope"]:
        from ai_skills.check_scopes import main as check_scope_main
        return check_scope_main(arguments[1:])
    parser = build_parser()
    args = parser.parse_args(arguments)
    if args.command == "failure-reason":
        return failure_reason(args)
    if args.command == "failure-class":
        return failure_class(args)
    if args.command == "synthesize":
        return synthesize(args)
    if args.command == "write-json":
        return write_json(args)
    parser.error(f"unknown command: {args.command}")

