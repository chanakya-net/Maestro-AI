"""Command-line entrypoint for the PR169 live fixture summary.

Usage: python -m examples.live_e2e_pr169_four.cli --label TEXT [--minutes N [N ...]]
"""

import argparse
import json
import sys

from examples.live_e2e_pr169_four.summary import build_summary

EXIT_OK = 0
EXIT_USAGE = 2


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m examples.live_e2e_pr169_four.cli",
        description="Print a normalized label and total minutes as JSON.",
    )
    parser.add_argument("--label", required=True, help="label to normalize (non-empty)")
    parser.add_argument(
        "--minutes",
        type=int,
        nargs="*",
        default=[],
        metavar="N",
        help="zero or more nonnegative integer durations in minutes",
    )
    return parser


def run_summary(label, minutes, stdout, stderr):
    """Build the summary and write JSON to stdout, or an error to stderr."""
    try:
        summary = build_summary(label, minutes)
    except (TypeError, ValueError) as exc:
        stderr.write(f"error: {exc}\n")
        return EXIT_USAGE
    stdout.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return EXIT_OK


def main(argv=None, stdout=None, stderr=None):
    """Parse argv and print the summary JSON; return the process exit status."""
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    return run_summary(args.label, args.minutes, stdout, stderr)


if __name__ == "__main__":
    raise SystemExit(main())
