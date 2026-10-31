import unittest
import math
from examples.runtime_smoke.stats.describe import describe_readings


class TestDescribeReadings(unittest.TestCase):
    def test_empty_input(self):
        """Empty iterable -> count=0, total=0, minimum/maximum/mean=None"""
        result = describe_readings([])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["total"], 0)
        self.assertIsNone(result["minimum"])
        self.assertIsNone(result["maximum"])
        self.assertIsNone(result["mean"])

    def test_singleton_input(self):
        """Singleton -> all stats equal that value, count=1"""
        result = describe_readings([42])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["total"], 42)
        self.assertEqual(result["minimum"], 42)
        self.assertEqual(result["maximum"], 42)
        self.assertEqual(result["mean"], 42)

    def test_mixed_negative_fractional_readings(self):
        """Mixed positive/negative/fractional -> conventional stats"""
        result = describe_readings([1, -2, 3.5, -0.5])
        self.assertEqual(result["count"], 4)
        self.assertEqual(result["total"], 2)  # 1 - 2 + 3.5 - 0.5
        self.assertEqual(result["minimum"], -2)
        self.assertEqual(result["maximum"], 3.5)
        self.assertEqual(result["mean"], 0.5)  # 2 / 4

    def test_generator_input_consumed_once(self):
        """Generator input consumed exactly once -> correct stats"""
        def gen():
            yield 10
            yield 20
            yield 30
        result = describe_readings(gen())
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["total"], 60)
        self.assertEqual(result["minimum"], 10)
        self.assertEqual(result["maximum"], 30)
        self.assertEqual(result["mean"], 20)

    def test_rejects_bool_nonnumeric_nan_infinity(self):
        """bool/non-numeric/NaN/infinity each raise ValueError"""
        # bool is int subclass -> must reject explicitly
        with self.assertRaises(ValueError):
            describe_readings([True])
        with self.assertRaises(ValueError):
            describe_readings([False])

        # non-numeric
        with self.assertRaises(ValueError):
            describe_readings(["string"])
        with self.assertRaises(ValueError):
            describe_readings([None])

        # NaN
        with self.assertRaises(ValueError):
            describe_readings([float("nan")])

        # infinity
        with self.assertRaises(ValueError):
            describe_readings([float("inf")])
        with self.assertRaises(ValueError):
            describe_readings([float("-inf")])

    def test_input_list_unchanged(self):
        """A list passed in is left unmodified after the call"""
        original = [5, 3, 1, 4, 2]
        original_copy = original.copy()
        describe_readings(original)
        self.assertEqual(original, original_copy)


if __name__ == "__main__":
    unittest.main()
