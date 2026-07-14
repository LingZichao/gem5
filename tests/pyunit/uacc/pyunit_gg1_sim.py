import math
import random
import unittest

from util.compare_uacc_models import _error_metrics, section14_moment_stats
from util.uacc_gg1_sim import (
    FeedbackCalibrator,
    MomentStats,
    allocation_vectors,
    build_allocation_trace,
    candidate_packet_rates,
    candidate_rate_sums,
    deterministic_arrivals,
    direct_queue_cost,
    kingman_wait,
    make_trace,
    measure_current_profile,
    mg1_wait,
    moments_from_candidate_rates,
    on_off_arrivals,
    oracle_score_allocation,
    poisson_arrivals,
    score_allocation,
    simulate_fifo,
    synchronized_bursts,
    window_queue_cost,
    window_sums_from_rates,
)


class GG1SimulationTest(unittest.TestCase):
    def test_section14_raw_sums_match_floating_moments(self):
        duration = 10_000.0
        packets = make_trace(
            on_off_arrivals(
                0.14, 100.0, 160.0, duration, random.Random(41)
            ),
            random.Random(42),
        )
        floating = MomentStats.from_packets(packets, 3.0, duration)
        raw_sums = section14_moment_stats(packets, 3.0, duration)
        self.assertAlmostEqual(raw_sums.arrival_rate, floating.arrival_rate)
        self.assertAlmostEqual(raw_sums.ca2, floating.ca2)
        self.assertAlmostEqual(raw_sums.service_mean, floating.service_mean)
        self.assertAlmostEqual(raw_sums.cs2, floating.cs2)
        self.assertAlmostEqual(raw_sums.utilization, floating.utilization)
        self.assertAlmostEqual(kingman_wait(raw_sums), kingman_wait(floating))

    def test_underestimation_rate_is_distinct_from_magnitude(self):
        metrics = _error_metrics([0.5, 2.0, 1.0], [1.0, 1.0, 0.0])
        self.assertEqual(metrics["under_rate_pct"], 50.0)
        self.assertEqual(metrics["under_mean_when_under"], 0.5)
        self.assertEqual(metrics["under_mean_all"], 0.25)

    def test_poisson_special_case_matches_mg1(self):
        stats = MomentStats.from_samples(
            [0.0, 0.0, 4.0],
            [1.0, 2.0, 1.0],
            duration=20.0,
        )
        self.assertAlmostEqual(stats.ca2, 1.0)
        self.assertAlmostEqual(
            kingman_wait(stats, use_effective_ca2=False),
            mg1_wait(stats),
        )

    def test_deterministic_arrivals_have_zero_raw_ca2(self):
        packets = make_trace(
            deterministic_arrivals(0.1, 100.0),
            random.Random(1),
            sizes=(8,),
            probabilities=(1.0,),
        )
        stats = MomentStats.from_packets(packets, 8.0, 100.0)
        self.assertAlmostEqual(stats.ca2, 0.0)
        self.assertEqual(stats.cs2, 0.0)
        self.assertEqual(kingman_wait(stats), mg1_wait(stats))

    def test_burstiness_increases_effective_gg1_prediction(self):
        regular = MomentStats.from_samples(
            [float(index * 10) for index in range(100)],
            [4.0] * 100,
            duration=1_000.0,
        )
        burst = MomentStats.from_samples(
            [float(index // 10 * 100 + index % 10) for index in range(100)],
            [4.0] * 100,
            duration=1_000.0,
        )
        self.assertGreater(burst.ca2, regular.ca2)
        self.assertGreater(kingman_wait(burst), kingman_wait(regular))

    def test_fifo_wait_and_finite_buffer_backpressure(self):
        trace = make_trace(
            [0.0, 0.0, 0.0, 0.0],
            random.Random(2),
            sizes=(8,),
            probabilities=(1.0,),
        )
        result = simulate_fifo(trace, 1.0, buffer_depth=1)
        self.assertEqual(result.backpressure_events, 2)
        self.assertEqual(result.max_occupancy, 2)
        self.assertAlmostEqual(result.observations[-1].queue_wait, 8.0)
        self.assertTrue(math.isclose(result.observations[-1].arrival, 16.0))

    def test_feedback_is_clipped_and_ewma_is_monotonic(self):
        calibrator = FeedbackCalibrator(alpha=1.0, beta_max=4.0)
        self.assertEqual(calibrator.update(100.0, 1.0), 4.0)
        self.assertEqual(calibrator.update(0.0, 1.0), 1.0)

    def test_candidate_allocation_rejects_saturation(self):
        misses = [poisson_arrivals(0.25, 10_000.0, random.Random(3))]
        score = score_allocation(
            misses,
            (1,),
            ([0.0, 1.0],),
            duration=10_000.0,
            bandwidth_bytes_per_cycle=1.0,
            lower_miss_penalty_cycles=100.0,
            fixed_latency_cycles=1.0,
            rho_max=0.90,
        )
        self.assertFalse(score.valid)
        self.assertTrue(math.isinf(score.objective))

    def test_candidate_reuses_current_ca2_and_recomputes_service_mix(self):
        duration = 10_000.0
        current_misses = [synchronized_bursts(100.0, 8, duration)]
        hit_probabilities = ([0.0, 0.10, 0.90],)
        profile = measure_current_profile(
            current_misses,
            (1,),
            hit_probabilities,
            duration=duration,
            bandwidth_bytes_per_cycle=3.0,
        )
        candidate = moments_from_candidate_rates(
            profile,
            (2,),
            hit_probabilities,
            duration=duration,
            bandwidth_bytes_per_cycle=3.0,
        )
        self.assertAlmostEqual(candidate.ca2, profile.stats.ca2)
        self.assertNotAlmostEqual(candidate.cs2, profile.stats.cs2)

    def test_direct_queue_cost_matches_full_gg1_candidate(self):
        duration = 10_000.0
        current_misses = [
            on_off_arrivals(
                0.14, 100.0, 160.0, duration, random.Random(51)
            )
        ]
        hit_probabilities = ([0.0, 0.20, 0.80],)
        profile = measure_current_profile(
            current_misses,
            (1,),
            hit_probabilities,
            duration=duration,
            bandwidth_bytes_per_cycle=3.0,
        )
        candidate = moments_from_candidate_rates(
            profile,
            (2,),
            hit_probabilities,
            duration=duration,
            bandwidth_bytes_per_cycle=3.0,
        )
        rates = candidate_packet_rates(
            profile,
            (2,),
            hit_probabilities,
            bandwidth_bytes_per_cycle=3.0,
        )
        sums = candidate_rate_sums(rates)
        direct_cost = direct_queue_cost(sums, profile.stats.ca2)
        self.assertAlmostEqual(
            direct_cost,
            candidate.arrival_rate * kingman_wait(candidate),
        )
        window_sums = window_sums_from_rates(sums, duration)
        window_cost = window_queue_cost(
            window_sums, duration, profile.stats.ca2
        )
        self.assertAlmostEqual(window_cost, duration * direct_cost)

    def test_allocation_prediction_is_based_on_current_window_only(self):
        duration = 10_000.0
        rng = random.Random(19)
        all_misses = [
            on_off_arrivals(0.14, 100.0, 160.0, 2.0 * duration, rng),
            poisson_arrivals(0.045, 2.0 * duration, rng),
        ]
        current = [
            [time for time in misses if time < duration]
            for misses in all_misses
        ]
        future = [
            [time - duration for time in misses if duration <= time]
            for misses in all_misses
        ]
        hit_probabilities = (
            [0.0, 0.42, 0.70, 0.80],
            [0.0, 0.10, 0.23, 0.30],
        )
        common = dict(
            duration=duration,
            bandwidth_bytes_per_cycle=3.0,
            lower_miss_penalty_cycles=300.0,
            fixed_latency_cycles=20.0,
            rho_max=0.90,
            buffer_depth=32,
        )
        profile = measure_current_profile(
            current,
            (1, 1),
            hit_probabilities,
            duration=duration,
            bandwidth_bytes_per_cycle=3.0,
        )
        current_queue = simulate_fifo(
            build_allocation_trace(current, (1, 1), hit_probabilities),
            3.0,
            buffer_depth=32,
        )
        calibrator = FeedbackCalibrator()
        calibrator.update(current_queue.mean_wait, kingman_wait(profile.stats))
        candidates = list(allocation_vectors(2, 3))
        oracle = max(
            (
                oracle_score_allocation(
                    future, allocation, hit_probabilities, **common
                )
                for allocation in candidates
            ),
            key=lambda score: score.objective,
        )
        feedback = max(
            (
                score_allocation(
                    current,
                    allocation,
                    hit_probabilities,
                    current_profile=profile,
                    base_beta=calibrator.beta,
                    use_feedback=True,
                    **common,
                )
                for allocation in candidates
            ),
            key=lambda score: score.objective,
        )
        direct_feedback = max(
            (
                score_allocation(
                    current,
                    allocation,
                    hit_probabilities,
                    current_profile=profile,
                    base_beta=calibrator.beta,
                    use_feedback=True,
                    model="direct",
                    **common,
                )
                for allocation in candidates
            ),
            key=lambda score: score.objective,
        )
        window_direct_feedback = max(
            (
                score_allocation(
                    current,
                    allocation,
                    hit_probabilities,
                    current_profile=profile,
                    base_beta=calibrator.beta,
                    use_feedback=True,
                    model="window_direct",
                    **common,
                )
                for allocation in candidates
            ),
            key=lambda score: score.objective,
        )
        self.assertEqual(oracle.allocation, (0, 3))
        self.assertEqual(feedback.allocation, oracle.allocation)
        self.assertEqual(direct_feedback.allocation, feedback.allocation)
        self.assertAlmostEqual(direct_feedback.objective, feedback.objective)
        self.assertEqual(
            window_direct_feedback.allocation, feedback.allocation
        )
        self.assertAlmostEqual(
            window_direct_feedback.objective, feedback.objective
        )
        self.assertEqual(feedback.observed_wait, 0.0)

    def test_synchronized_bursts_are_more_variable_than_regular_stream(self):
        bursts = synchronized_bursts(100.0, 8, 10_000.0)
        regular = deterministic_arrivals(0.08, 10_000.0)
        self.assertGreater(
            MomentStats.from_samples(
                bursts, [1.0] * len(bursts), 10_000.0
            ).ca2,
            MomentStats.from_samples(
                regular, [1.0] * len(regular), 10_000.0
            ).ca2,
        )

    def test_gg1_ranking_tracks_measured_oracle_better_than_mg1(self):
        duration = 10_000.0
        rng = random.Random(19)
        core_misses = [
            on_off_arrivals(0.14, 100.0, 160.0, duration, rng),
            poisson_arrivals(0.045, duration, rng),
        ]
        hit_probabilities = ([0.0, 0.42, 0.70, 0.80], [0.0, 0.10, 0.23, 0.30])
        common = dict(
            duration=duration,
            bandwidth_bytes_per_cycle=3.0,
            lower_miss_penalty_cycles=300.0,
            fixed_latency_cycles=20.0,
            rho_max=0.90,
            buffer_depth=32,
        )
        candidates = list(allocation_vectors(2, 3))
        oracle = max(
            (
                oracle_score_allocation(
                    core_misses, allocation, hit_probabilities, **common
                )
                for allocation in candidates
            ),
            key=lambda score: score.objective,
        )
        mg1 = max(
            (
                score_allocation(
                    core_misses,
                    allocation,
                    hit_probabilities,
                    model="mg1",
                    **common,
                )
                for allocation in candidates
            ),
            key=lambda score: score.objective,
        )
        gg1 = max(
            (
                score_allocation(
                    core_misses,
                    allocation,
                    hit_probabilities,
                    model="gg1",
                    **common,
                )
                for allocation in candidates
            ),
            key=lambda score: score.objective,
        )
        self.assertEqual(oracle.allocation, (0, 3))
        self.assertEqual(gg1.allocation, oracle.allocation)
        self.assertNotEqual(mg1.allocation, oracle.allocation)


if __name__ == "__main__":
    unittest.main()
