# Four-issue live runtime fixture

This isolated fixture validates PR169 routing, concurrent helpers, dependency ordering, delegated review, commits, shared-branch merge, push and GitHub updates. It is an experiment branch only.

All code uses Python standard library. Each issue owns its stated files. Tests should use unittest. Final suite: `python -m unittest discover -s examples/live_e2e_pr169_four -p "test_*.py" -v`.

Package contract: labels.normalize_label, durations.total_minutes, summary.build_summary, CLI via `python -m examples.live_e2e_pr169_four.cli`.
