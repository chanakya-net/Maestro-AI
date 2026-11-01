import unittest

from examples.live_e2e_pr169_four.summary import build_summary


class BuildSummaryTests(unittest.TestCase):
    def test_normalizes_unicode_label_and_returns_exact_summary(self):
        result = build_summary("  Ünïcödé 日本語  ", [12, 8])

        self.assertEqual(result, {"label": "ünïcödé 日本語", "total_minutes": 20})

    def test_consumes_duration_generator_once(self):
        consumed = []

        def durations():
            for value in (3, 7, 11):
                consumed.append(value)
                yield value

        result = build_summary("Trip", durations())

        self.assertEqual(result, {"label": "trip", "total_minutes": 21})
        self.assertEqual(consumed, [3, 7, 11])

    def test_empty_durations_return_zero(self):
        self.assertEqual(
            build_summary("Empty", []),
            {"label": "empty", "total_minutes": 0},
        )

    def test_invalid_label_preserves_helper_error(self):
        with self.assertRaisesRegex(
            ValueError, "^label must not be empty or whitespace-only$"
        ):
            build_summary("  ", [5])

    def test_negative_duration_preserves_helper_error(self):
        with self.assertRaisesRegex(ValueError, "^durations must be nonnegative$"):
            build_summary("Trip", [5, -1])

    def test_boolean_duration_preserves_helper_error(self):
        with self.assertRaisesRegex(TypeError, "^durations must be integers$"):
            build_summary("Trip", [True])


if __name__ == "__main__":
    unittest.main()
