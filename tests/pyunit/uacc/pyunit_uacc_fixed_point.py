import unittest

from util.uacc_hw.fixed_point_sweep import (
    Format,
    quantize_unsigned,
    reciprocal_divide,
    utilization_limit,
)
from util.uacc_hw.range_precision_sweep import (
    CandidateWindow,
    FormatMetrics,
    RangeAssumptions,
    SweepFormat,
    derive_widths,
    fixed_objective,
    quantize_unsigned as quantize_range_unsigned,
    reciprocal_divide_q16,
    unsigned_bits,
)


class UaccFixedPointTest(unittest.TestCase):
    def test_unsigned_quantization_truncates_and_saturates(self):
        fmt = Format(4, 8, 12)
        value, saturated = quantize_unsigned(1.1, fmt)
        self.assertEqual(value, 1.09765625)
        self.assertFalse(saturated)
        value, saturated = quantize_unsigned(16.0, fmt)
        self.assertEqual(value, 15.99609375)
        self.assertTrue(saturated)

    def test_reciprocal_division_error_decreases_with_precision(self):
        exact = 12345.0 / 678.0
        low = reciprocal_divide(12345.0, 678.0, 8)
        high = reciprocal_divide(12345.0, 678.0, 20)
        self.assertLessEqual(abs(high - exact), abs(low - exact))

    def test_utilization_limit_uses_datapath_fractional_width(self):
        self.assertEqual(utilization_limit(None), 0.9)
        self.assertEqual(utilization_limit(Format(1, 1, 16)), 0.5)
        self.assertEqual(utilization_limit(Format(1, 2, 16)), 0.75)
        self.assertEqual(utilization_limit(Format(1, 4, 16)), 0.875)

    def test_range_widths_shrink_the_large_accumulators(self):
        widths = derive_widths(RangeAssumptions(100_000, 100_000))
        self.assertEqual(widths.count_bits, 17)
        self.assertEqual(widths.window_bits, 17)
        self.assertLess(widths.service_second_sum_bits, 40)
        self.assertLess(widths.total_cost_integer_bits, 50)
        self.assertLess(widths.shared_divider_integer_bits, 80)

    def test_range_width_override_changes_only_selected_field(self):
        fmt = SweepFormat(
            "lossy",
            RangeAssumptions(100_000, 100_000),
            4,
            width_overrides=(("busy_sum_bits", 17),),
        )
        baseline = derive_widths(fmt.bounds)
        self.assertEqual(fmt.widths.busy_sum_bits, 17)
        self.assertEqual(
            fmt.widths.service_second_sum_bits,
            baseline.service_second_sum_bits,
        )

    def test_lossy_traffic_overflow_invalidates_candidate(self):
        fmt = SweepFormat(
            "lossy",
            RangeAssumptions(100_000, 100_000),
            4,
            width_overrides=(
                ("busy_sum_bits", 16),
                ("service_second_sum_bits", 20),
            ),
        )
        metrics = FormatMetrics(fmt, 1)
        candidate = CandidateWindow(
            allocation=(2, 0),
            r0=4760,
            r1=65_688,
            r2=1_755_488,
            fixed_cost=95_200,
            capacity_gain=499_800,
        )
        objective = fixed_objective(
            candidate, 100_000, 1.0, 1.0, fmt, metrics
        )
        self.assertEqual(objective, float("-inf"))
        self.assertEqual(metrics.saturation_counts["r1"], 1)
        self.assertEqual(metrics.saturation_counts["r2"], 1)

    def test_unsigned_bits_includes_the_maximum_value(self):
        self.assertEqual(unsigned_bits(0), 1)
        self.assertEqual(unsigned_bits(1), 1)
        self.assertEqual(unsigned_bits(2), 2)
        self.assertEqual(unsigned_bits(100_000), 17)

    def test_rho_can_keep_independent_precision(self):
        rho_q, saturated = quantize_range_unsigned(
            0.9, 1, 8, "truncate"
        )
        self.assertFalse(saturated)
        self.assertEqual(rho_q / 256.0, 0.8984375)

    def test_q16_reciprocal_matches_exact_division_closely(self):
        numerator = 12_345_678_901
        denominator = 54_321
        approximate = reciprocal_divide_q16(numerator, denominator)
        exact = numerator // denominator
        self.assertLess(abs(approximate - exact), max(2, exact // 1000))


if __name__ == "__main__":
    unittest.main()
