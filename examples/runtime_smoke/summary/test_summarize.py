import unittest

from examples.runtime_smoke.summary.summarize import summarize_readings


class SummarizeReadingsTests(unittest.TestCase):
    def test_default_bounds_returns_clamped_values_and_exact_statistics(self):
        readings = [-5, 12.5, 150]

        result = summarize_readings(readings)

        self.assertEqual(result["values"], [0, 12.5, 100])
        self.assertEqual(
            result,
            {
                "count": 3,
                "total": 112.5,
                "minimum": 0,
                "maximum": 100,
                "mean": 37.5,
                "values": [0, 12.5, 100],
            },
        )
        self.assertEqual(readings, [-5, 12.5, 150])

    def test_empty_and_single_pass_generator_inputs(self):
        self.assertEqual(
            summarize_readings([]),
            {
                "count": 0,
                "total": 0,
                "minimum": None,
                "maximum": None,
                "mean": None,
                "values": [],
            },
        )

        class SinglePass:
            def __init__(self, readings):
                self.readings = readings
                self.iterated = False

            def __iter__(self):
                if self.iterated:
                    raise AssertionError("values were iterated more than once")
                self.iterated = True
                return iter(self.readings)

        values = SinglePass([10, 20, 30])
        self.assertEqual(summarize_readings(values)["values"], [10, 20, 30])
        self.assertTrue(values.iterated)

    def test_custom_bounds_clamp_before_describing(self):
        result = summarize_readings(
            [-10, -4, 1, 7], minimum=-5, maximum=3
        )

        self.assertEqual(result["values"], [-5, -4, 1, 3])
        self.assertEqual(result["count"], 4)
        self.assertEqual(result["total"], -5)
        self.assertEqual(result["minimum"], -5)
        self.assertEqual(result["maximum"], 3)
        self.assertEqual(result["mean"], -1.25)

    def test_invalid_bounds_and_readings_propagate(self):
        invalid_values = [True, "10", float("nan"), float("inf")]
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    summarize_readings([value])

        for parameter, value in (
            ("minimum", float("nan")),
            ("maximum", float("inf")),
        ):
            with self.subTest(parameter=parameter, value=value):
                with self.assertRaises(ValueError):
                    summarize_readings([1], **{parameter: value})

        class MustNotIterate:
            def __iter__(self):
                raise AssertionError("invalid bounds should fail first")

        with self.assertRaises(ValueError):
            summarize_readings(MustNotIterate(), minimum=2, maximum=1)


if __name__ == "__main__":
    unittest.main()
