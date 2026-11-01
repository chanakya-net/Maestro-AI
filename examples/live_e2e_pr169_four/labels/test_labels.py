import unittest

from examples.live_e2e_pr169_four.labels import normalize_label


class NormalizeLabelBehaviorTests(unittest.TestCase):
    def test_plain_text_is_lowercased_and_trimmed(self):
        self.assertEqual(normalize_label("  Bug Report  "), "bug report")

    def test_tabs_and_newlines_collapse_to_single_space(self):
        self.assertEqual(normalize_label("\tHigh\n\nPriority\r\nTask"), "high priority task")

    def test_interior_runs_of_whitespace_collapse(self):
        self.assertEqual(normalize_label("needs   \t  review"), "needs review")

    def test_unicode_text_is_preserved_and_lowercased(self):
        self.assertEqual(normalize_label("  Ünïcödé 日本語  "), "ünïcödé 日本語")

    def test_unicode_uppercase_is_lowercased(self):
        self.assertEqual(normalize_label("ÉCOLE"), "école")

    def test_punctuation_is_preserved(self):
        self.assertEqual(normalize_label("  Fix: API/v2 (urgent)!  "), "fix: api/v2 (urgent)!")

    def test_hyphen_and_dot_punctuation_is_preserved(self):
        self.assertEqual(normalize_label("Area.Core-Lib"), "area.core-lib")


class NormalizeLabelRejectionTests(unittest.TestCase):
    def test_empty_string_raises_value_error(self):
        with self.assertRaises(ValueError):
            normalize_label("")

    def test_whitespace_only_raises_value_error(self):
        with self.assertRaises(ValueError):
            normalize_label(" \t\n\r ")

    def test_non_string_none_raises_type_error(self):
        with self.assertRaises(TypeError):
            normalize_label(None)

    def test_non_string_int_raises_type_error(self):
        with self.assertRaises(TypeError):
            normalize_label(42)

    def test_bytes_raise_type_error(self):
        with self.assertRaises(TypeError):
            normalize_label(b"label")


if __name__ == "__main__":
    unittest.main()
