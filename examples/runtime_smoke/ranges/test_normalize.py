import math
import unittest

from normalize import normalize_readings


class NormalizeReadingsTests(unittest.TestCase):
    def test_default_bounds_clamp_and_preserve_input(self):
        readings = [-5, 20, 150]

        result = normalize_readings(readings)

        self.assertEqual(result, [0, 20, 100])
        self.assertIsNot(result, readings)
        self.assertEqual(readings, [-5, 20, 150])
        self.assertEqual(normalize_readings([]), [])

    def test_custom_bounds_and_single_pass_iterator(self):
        class SinglePass:
            def __init__(self, readings):
                self.readings = readings
                self.iterated = False

            def __iter__(self):
                if self.iterated:
                    raise AssertionError("values were iterated more than once")
                self.iterated = True
                return iter(self.readings)

        values = SinglePass([-10, -4, 1])

        self.assertEqual(
            normalize_readings(values, minimum=-5, maximum=-2),
            [-5, -4, -2],
        )
        self.assertEqual(normalize_readings([1, -1], minimum=3, maximum=3), [3, 3])

    def test_invalid_bounds_and_readings_raise_value_error(self):
        invalid_values = [True, "10", math.nan, math.inf, -math.inf]
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_readings([value])

        invalid_bounds = [True, "0", math.nan, math.inf, -math.inf]
        for bound in invalid_bounds:
            for parameter in ("minimum", "maximum"):
                with self.subTest(bound=bound, parameter=parameter):
                    with self.assertRaises(ValueError):
                        normalize_readings([1], **{parameter: bound})

        with self.assertRaises(ValueError):
            normalize_readings([1], minimum=2, maximum=1)

        values = iter([1])
        with self.assertRaises(ValueError):
            normalize_readings(values, minimum=math.nan)
        self.assertEqual(next(values), 1)


if __name__ == "__main__":
    unittest.main()
