# Four-issue live runtime fixture

This isolated fixture validates PR169 routing, concurrent helpers, dependency ordering, delegated review, commits, shared-branch merge, push and GitHub updates. It is an experiment branch only.

All code uses Python standard library. Each issue owns its stated files. Tests should use unittest. Final suite: `python -m unittest discover -s examples/live_e2e_pr169_four -p "test_*.py" -v`.

Package contract: labels.normalize_label, durations.total_minutes, summary.build_summary, CLI via `python -m examples.live_e2e_pr169_four.cli`.

## CLI usage

Run from the repository root:

```sh
python -m examples.live_e2e_pr169_four.cli --label "  Café   meeting  " --minutes 5 10 0
```

Output (exit 0):

```json
{"label": "café meeting", "total_minutes": 15}
```

Flags:

- `--label TEXT` (required): trimmed, whitespace-collapsed, lowercased; must be non-empty.
- `--minutes N [N ...]` (optional, default none): zero or more nonnegative integers; no values gives `total_minutes` 0.

Exit statuses:

- `0`: JSON summary printed to stdout.
- `2`: invalid label, negative minutes (`error: ...` on stderr), or argparse usage error such as a non-integer minute or missing `--label`.

Tests: `python -m unittest examples.live_e2e_pr169_four.test_cli -v`.
