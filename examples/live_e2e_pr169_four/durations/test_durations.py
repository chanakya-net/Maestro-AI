import unittest

from examples.live_e2e_pr169_four.durations import total_minutes


class TotalMinutesTests(unittest.TestCase):
    def test_empty_iterable_returns_zero(self):
        self.assertEqual(total_minutes([]), 0)

    def test_zero_values_return_zero(self):
        self.assertEqual(total_minutes([0, 0]), 0)

    def test_sums_multiple_integer_values(self):
        self.assertEqual(total_minutes([12, 35, 8]), 55)

    def test_accepts_generator(self):
        values = (value for value in (3, 7, 11))
        self.assertEqual(total_minutes(values), 21)

    def test_rejects_negative_integer(self):
        with self.assertRaises(ValueError):
            total_minutes([4, -1, 2])

    def test_rejects_boolean_even_though_it_is_an_int_subclass(self):
        for value in (True, False):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    total_minutes([value])

    def test_rejects_non_integer_values(self):
        for value in (1.5, "2", None, [], {}):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    total_minutes([value])


if __name__ == "__main__":
    unittest.main()
