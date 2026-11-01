import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from examples.live_e2e_pr169_four.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE = "examples.live_e2e_pr169_four.cli"


def run_cli(*args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-m", MODULE, *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def call_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class CliSuccessTests(unittest.TestCase):
    def test_prints_json_summary_and_returns_zero(self):
        code, out, err = call_main(["--label", "Trip", "--minutes", "5", "10"])

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"label": "trip", "total_minutes": 15})
        self.assertEqual(err, "")

    def test_subprocess_success_prints_json_object(self):
        result = run_cli("--label", "Trip", "--minutes", "5", "10")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"label": "trip", "total_minutes": 15})

    def test_subprocess_unicode_label_and_whitespace_are_normalized(self):
        result = run_cli("--label", "  Café   meeting  ", "--minutes", "5", "10", "0")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"label": "café meeting", "total_minutes": 15},
        )
        self.assertIn("café meeting", result.stdout)

    def test_empty_minutes_flag_yields_zero_total(self):
        code, out, _ = call_main(["--label", "Empty", "--minutes"])

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"label": "empty", "total_minutes": 0})

    def test_omitted_minutes_defaults_to_zero_total(self):
        result = run_cli("--label", "Nothing")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"label": "nothing", "total_minutes": 0})


class CliErrorTests(unittest.TestCase):
    def test_whitespace_label_is_rejected_with_exit_two(self):
        result = run_cli("--label", "   ", "--minutes", "5")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("label", result.stderr)

    def test_negative_minutes_are_rejected_with_exit_two(self):
        result = run_cli("--label", "Trip", "--minutes", "5", "-3")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("nonnegative", result.stderr)

    def test_negative_minutes_in_process_return_two(self):
        code, out, err = call_main(["--label", "Trip", "--minutes", "-1"])

        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("nonnegative", err)

    def test_non_integer_minutes_are_usage_error_with_exit_two(self):
        result = run_cli("--label", "Trip", "--minutes", "5", "abc")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("invalid int value", result.stderr)

    def test_non_integer_minutes_in_process_return_two(self):
        code, out, err = call_main(["--label", "Trip", "--minutes", "1.5"])

        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("invalid int value", err)

    def test_missing_label_is_usage_error_with_exit_two(self):
        result = run_cli("--minutes", "5")

        self.assertEqual(result.returncode, 2)
        self.assertIn("--label", result.stderr)


if __name__ == "__main__":
    unittest.main()
