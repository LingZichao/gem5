import random
import unittest

from util.uacc_hw.gg1_ucp_ablation import (
    HIT_PROBABILITIES,
    POLICIES,
    TraceConfig,
    _aggregate,
    _candidate_vectors,
    _oracle_window,
    _phase_arrivals,
    generate_trace,
    run_policy,
)


class GG1UCPAblationTest(unittest.TestCase):
    def test_phase_arrivals_stay_inside_the_window(self):
        for phase in (
            "steady", "burst", "synchronized", "recovery"
        ):
            arrivals = _phase_arrivals(
                phase, 2_000.0, 4_000.0, 0.05, random.Random(7)
            )
            self.assertTrue(arrivals)
            self.assertTrue(all(2_000.0 <= value < 4_000.0
                                for value in arrivals))

    def test_oracle_bounds_every_causal_policy_window(self):
        config = TraceConfig(window=500.0, windows=4, max_ways=4)
        traces = generate_trace(config, seed=3, rate_scale=1.2)
        candidates = _candidate_vectors(config)
        for policy in POLICIES:
            run = run_policy(
                policy,
                traces,
                HIT_PROBABILITIES,
                candidates,
                config,
            )
            self.assertEqual(run.windows[0].allocation, (1, 1, 1, 1))
            for trace, result in zip(traces, run.windows):
                _, oracle_utility = _oracle_window(
                    trace,
                    HIT_PROBABILITIES,
                    candidates,
                    config,
                )
                self.assertLessEqual(
                    result.utility, oracle_utility + 1.0e-12
                )

    def test_aggregate_groups_seeds_at_the_same_scale(self):
        rows = [
            {
                "policy": "ucp",
                "scale": 1.0,
                "reference_rho": 0.20,
                "utility_ratio": 0.8,
            },
            {
                "policy": "ucp",
                "scale": 1.0,
                "reference_rho": 0.22,
                "utility_ratio": 1.0,
            },
        ]
        result = _aggregate(rows, "utility_ratio")
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["reference_rho"], 0.21)
        self.assertAlmostEqual(result[0]["mean"], 0.9)

    def test_contention_case_changes_feedback_decisions(self):
        config = TraceConfig(
            burst_bandwidth_factor=0.45,
            synchronized_bandwidth_factor=0.55,
        )
        traces = generate_trace(config, seed=0, rate_scale=0.9)
        candidates = _candidate_vectors(config)
        gg1 = run_policy(
            "ucp_gg1", traces, HIT_PROBABILITIES, candidates, config
        )
        feedback = run_policy(
            "ucp_gg1_feedback",
            traces,
            HIT_PROBABILITIES,
            candidates,
            config,
        )
        self.assertTrue(any(
            left.allocation != right.allocation
            for left, right in zip(gg1.windows, feedback.windows)
        ))
        self.assertNotAlmostEqual(
            sum(item.utility for item in gg1.windows),
            sum(item.utility for item in feedback.windows),
        )


if __name__ == "__main__":
    unittest.main()
