#!/usr/bin/env python3
"""Compare the legacy UACC queue estimate with the Section 14 path.

The Section 14 implementation evaluates the raw-sum moment formulas and the
shift-EWMA behavior using ideal Python arithmetic.  It intentionally does not
pretend to quantify RTL fixed-point error because model.md does not yet fix a
Q-format, reciprocal approximation, or saturation width.
"""

from __future__ import annotations

import argparse
import math
import random
import statistics
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Iterable, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from util.uacc_gg1_sim import (  # noqa: E402
    CandidateRateSums,
    FeedbackCalibrator,
    MomentStats,
    _scenario_trace,
    allocation_vectors,
    build_allocation_trace,
    direct_queue_cost,
    kingman_wait,
    measure_current_profile,
    mg1_wait,
    on_off_arrivals,
    oracle_score_allocation,
    poisson_arrivals,
    score_allocation,
    simulate_fifo,
    window_queue_cost,
    window_sums_from_rates,
)


EPSILON = 1.0e-12


@dataclass(frozen=True)
class WindowSample:
    observed_wait: float
    legacy_wait: float
    mg1_wait: float
    gg1_wait: float
    gg1_feedback_wait: float
    section14_raw_wait: float
    section14_count_wait: float
    section14_wait: float
    section14_feedback_wait: float
    ca2_raw: float
    ca2_ewma: float


def legacy_wait(
    stats: MomentStats,
    *,
    rtt_cycles: float,
    cache_line_size: int,
    bandwidth_bytes_per_cycle: float,
    rho_limit: float = 0.999999,
) -> float:
    """The plan.md aggregate-lambda, RTT/serialization M/G/1 estimate."""

    service = max(rtt_cycles, cache_line_size / bandwidth_bytes_per_cycle)
    rho = stats.arrival_rate * service
    if stats.arrival_rate <= 0.0:
        return 0.0
    if rho >= rho_limit:
        return math.inf
    return stats.arrival_rate * service * service / (2.0 * (1.0 - rho))


def shift_ewma(previous: float, sample: float, shift: int) -> float:
    return previous + (sample - previous) / (2**shift)


def section14_moment_stats(
    packets,
    bandwidth_bytes_per_cycle: float,
    duration: float,
) -> MomentStats:
    """Compute moments using the raw-sum equations from model.md Section 14."""

    if duration <= 0.0:
        raise ValueError("duration must be positive")
    if bandwidth_bytes_per_cycle <= 0.0:
        raise ValueError("bandwidth must be positive")
    count = len(packets)
    if count == 0:
        return MomentStats.from_samples([], [], duration)

    arrivals = [packet.offered_arrival for packet in packets]
    interarrivals = [
        right - left for left, right in zip(arrivals, arrivals[1:])
    ]
    interarrival_count = len(interarrivals)
    interarrival_sum = math.fsum(interarrivals)
    interarrival_square_sum = math.fsum(
        value * value for value in interarrivals
    )
    if interarrival_count == 0:
        interarrival_mean = 0.0
        ca2 = 1.0
    elif interarrival_sum <= EPSILON:
        interarrival_mean = 0.0
        ca2 = math.inf
    else:
        interarrival_mean = interarrival_sum / interarrival_count
        ca2 = max(
            0.0,
            interarrival_count
            * interarrival_square_sum
            / (interarrival_sum * interarrival_sum)
            - 1.0,
        )

    services = [
        packet.size_bytes / bandwidth_bytes_per_cycle for packet in packets
    ]
    service_sum = math.fsum(services)
    service_square_sum = math.fsum(value * value for value in services)
    service_mean = service_sum / count
    service_second_moment = service_square_sum / count
    if service_sum <= EPSILON:
        cs2 = 0.0
        utilization = 0.0
    else:
        cs2 = max(
            0.0,
            count * service_square_sum / (service_sum * service_sum) - 1.0,
        )
        utilization = service_sum / duration

    return MomentStats(
        count=count,
        duration=duration,
        arrival_rate=count / duration,
        interarrival_mean=interarrival_mean,
        ca2=ca2,
        service_mean=service_mean,
        service_second_moment=service_second_moment,
        cs2=cs2,
        utilization=utilization,
    )


def _window_samples(
    packets,
    queue,
    *,
    duration: float,
    window: float,
    bandwidth: float,
    rtt_cycles: float,
    cache_line_size: int,
    ewma_shift: int,
    feedback_shift: int,
    beta_max: float,
) -> list[WindowSample]:
    observations = {
        item.packet.sequence: item for item in queue.observations
    }
    ca2_ewma = 1.0
    beta_gg1 = 1.0
    beta_section14 = 1.0
    samples = []
    windows = int(math.ceil(duration / window))
    for index in range(windows):
        start = index * window
        end = min(duration, start + window)
        offered = [
            packet
            for packet in packets
            if start <= packet.offered_arrival < end
        ]
        if not offered:
            continue
        observed = [observations[packet.sequence] for packet in offered]
        stats = MomentStats.from_packets(
            offered, bandwidth, max(end - start, EPSILON)
        )
        section14_raw_stats = section14_moment_stats(
            offered, bandwidth, max(end - start, EPSILON)
        )
        observed_wait = statistics.fmean(
            item.queue_wait for item in observed
        )

        raw_gg1 = kingman_wait(stats)
        gg1_feedback = (
            beta_gg1 * raw_gg1 if math.isfinite(raw_gg1) else raw_gg1
        )
        raw_beta = _feedback_ratio(observed_wait, raw_gg1, beta_max)

        section14_sums = CandidateRateSums(
            l0=section14_raw_stats.arrival_rate,
            l1=section14_raw_stats.utilization,
            l2=(
                section14_raw_stats.arrival_rate
                * section14_raw_stats.service_second_moment
            ),
        )
        section14_raw_cost = direct_queue_cost(
            section14_sums, section14_raw_stats.ca2
        )
        section14_raw_wait = (
            section14_raw_cost / section14_sums.l0
            if section14_sums.l0 > 0.0
            else 0.0
        )
        section14_window_sums = window_sums_from_rates(
            section14_sums, max(end - start, EPSILON)
        )
        section14_count_cost = window_queue_cost(
            section14_window_sums,
            max(end - start, EPSILON),
            section14_raw_stats.ca2,
        )
        section14_count_wait = (
            section14_count_cost / section14_window_sums.r0
            if section14_window_sums.r0 > 0.0
            else 0.0
        )
        ca2_ewma = shift_ewma(
            ca2_ewma, section14_raw_stats.ca2, ewma_shift
        )
        section14_stats = MomentStats(
            count=section14_raw_stats.count,
            duration=section14_raw_stats.duration,
            arrival_rate=section14_raw_stats.arrival_rate,
            interarrival_mean=section14_raw_stats.interarrival_mean,
            ca2=max(1.0, ca2_ewma),
            service_mean=section14_raw_stats.service_mean,
            service_second_moment=section14_raw_stats.service_second_moment,
            cs2=section14_raw_stats.cs2,
            utilization=section14_raw_stats.utilization,
        )
        section14_cost = direct_queue_cost(
            section14_sums, section14_stats.ca2
        )
        section14_wait = (
            section14_cost / section14_sums.l0
            if section14_sums.l0 > 0.0
            else 0.0
        )
        section14_feedback = (
            beta_section14 * section14_wait
            if math.isfinite(section14_wait)
            else section14_wait
        )
        section14_raw_beta = _feedback_ratio(
            observed_wait, section14_wait, beta_max
        )

        samples.append(
            WindowSample(
                observed_wait=observed_wait,
                legacy_wait=legacy_wait(
                    stats,
                    rtt_cycles=rtt_cycles,
                    cache_line_size=cache_line_size,
                    bandwidth_bytes_per_cycle=bandwidth,
                ),
                mg1_wait=mg1_wait(stats),
                gg1_wait=raw_gg1,
                gg1_feedback_wait=gg1_feedback,
                section14_raw_wait=section14_raw_wait,
                section14_count_wait=section14_count_wait,
                section14_wait=section14_wait,
                section14_feedback_wait=section14_feedback,
                ca2_raw=stats.ca2,
                ca2_ewma=ca2_ewma,
            )
        )

        # All feedback updates happen after the current window's prediction.
        beta_gg1 = shift_ewma(beta_gg1, raw_beta, feedback_shift)
        if math.isfinite(section14_wait):
            beta_section14 = shift_ewma(
                beta_section14, section14_raw_beta, feedback_shift
            )
    return samples


def _feedback_ratio(observed: float, model: float, beta_max: float) -> float:
    if not math.isfinite(model) or model <= EPSILON:
        return beta_max if observed > EPSILON else 1.0
    return min(max(observed / model, 1.0), beta_max)


def _error_metrics(
    predictions: Iterable[float], observations: Sequence[float]
) -> dict[str, float | int]:
    values = list(predictions)
    absolute = []
    relative = []
    under_magnitude_all = []
    under_magnitude_when_under = []
    valid_nonzero = 0
    under_count = 0
    invalid = 0
    for prediction, observation in zip(values, observations):
        if not math.isfinite(prediction):
            invalid += 1
            continue
        absolute.append(abs(prediction - observation))
        if observation > EPSILON:
            valid_nonzero += 1
            relative.append(abs(prediction - observation) / observation)
            magnitude = max(0.0, observation - prediction) / observation
            under_magnitude_all.append(magnitude)
            if prediction < observation:
                under_count += 1
                under_magnitude_when_under.append(magnitude)
    return {
        "windows": len(values),
        "invalid_pct": 100.0 * invalid / max(len(values), 1),
        "mae": statistics.fmean(absolute) if absolute else math.inf,
        "mare_nonzero": (
            statistics.fmean(relative) if relative else math.inf
        ),
        "p95_rel_nonzero": (
            _percentile(relative, 0.95) if relative else math.inf
        ),
        "under_rate_pct": 100.0 * under_count / max(valid_nonzero, 1),
        "under_mean_all": (
            statistics.fmean(under_magnitude_all)
            if under_magnitude_all
            else 0.0
        ),
        "under_mean_when_under": (
            statistics.fmean(under_magnitude_when_under)
            if under_magnitude_when_under
            else 0.0
        ),
    }


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def evaluate(
    *,
    seeds: int,
    duration: float,
    bandwidth: float,
    window: float,
) -> dict[str, object]:
    model_names = (
        "legacy",
        "mg1",
        "gg1",
        "gg1_feedback",
        "section14_raw",
        "section14",
        "section14_feedback",
    )
    predictions = {name: [] for name in model_names}
    observations = []
    scenario_predictions = {
        scenario: {name: [] for name in model_names}
        for scenario in ("poisson", "deterministic", "onoff", "synchronized")
    }
    scenario_observations = {scenario: [] for scenario in scenario_predictions}
    ca2_raw = []
    ca2_ewma = []
    equivalence_errors = []
    count_equivalence_errors = []
    for seed in range(seeds):
        for scenario in scenario_predictions:
            packets = _scenario_trace(scenario, duration, seed)
            queue = simulate_fifo(packets, bandwidth, buffer_depth=8)
            samples = _window_samples(
                packets,
                queue,
                duration=duration,
                window=window,
                bandwidth=bandwidth,
                rtt_cycles=4.0,
                cache_line_size=64,
                ewma_shift=2,
                feedback_shift=2,
                beta_max=4.0,
            )
            sample_observations = [item.observed_wait for item in samples]
            observations.extend(sample_observations)
            scenario_observations[scenario].extend(sample_observations)
            ca2_raw.extend(item.ca2_raw for item in samples)
            ca2_ewma.extend(item.ca2_ewma for item in samples)
            for sample in samples:
                if math.isfinite(sample.gg1_wait) and math.isfinite(
                    sample.section14_raw_wait
                ):
                    equivalence_errors.append(
                        abs(sample.gg1_wait - sample.section14_raw_wait)
                    )
                if math.isfinite(sample.section14_raw_wait) and math.isfinite(
                    sample.section14_count_wait
                ):
                    count_equivalence_errors.append(
                        abs(
                            sample.section14_raw_wait
                            - sample.section14_count_wait
                        )
                    )
                for name in model_names:
                    value = getattr(sample, f"{name}_wait")
                    predictions[name].append(value)
                    scenario_predictions[scenario][name].append(value)
    result: dict[str, object] = {}
    for name, values in predictions.items():
        result[name] = _error_metrics(values, observations)
    result["by_scenario"] = {
        scenario: {
            name: _error_metrics(values, scenario_observations[scenario])
            for name, values in models.items()
        }
        for scenario, models in scenario_predictions.items()
    }
    result["meta"] = {
        "mean_ca2_raw": statistics.fmean(ca2_raw),
        "mean_ca2_ewma": statistics.fmean(ca2_ewma),
        "max_raw_equivalence_error": (
            max(equivalence_errors) if equivalence_errors else 0.0
        ),
        "max_count_equivalence_error": (
            max(count_equivalence_errors)
            if count_equivalence_errors
            else 0.0
        ),
    }
    return result


def evaluate_allocation_equivalence(
    *, seeds: int, duration: float
) -> dict[str, float | int]:
    """Compare full and direct feedback allocation over causal windows."""

    hit_probabilities = (
        [0.0, 0.42, 0.70, 0.80],
        [0.0, 0.10, 0.23, 0.30],
    )
    candidates = list(allocation_vectors(2, 3))
    agreement = 0
    window_agreement = 0
    full_oracle_agreement = 0
    direct_oracle_agreement = 0
    window_oracle_agreement = 0
    objective_differences = []
    window_objective_differences = []
    selected_objective_differences = []
    full_regrets = []
    direct_regrets = []
    window_regrets = []

    for seed in range(seeds):
        rng = random.Random(seed)
        all_core_misses = [
            on_off_arrivals(0.14, 100.0, 160.0, 2.0 * duration, rng),
            poisson_arrivals(0.045, 2.0 * duration, rng),
        ]
        current = [
            [time for time in misses if time < duration]
            for misses in all_core_misses
        ]
        future = [
            [
                time - duration
                for time in misses
                if duration <= time < 2.0 * duration
            ]
            for misses in all_core_misses
        ]
        common = dict(
            duration=duration,
            bandwidth_bytes_per_cycle=3.0,
            lower_miss_penalty_cycles=300.0,
            fixed_latency_cycles=20.0,
            rho_max=0.90,
            buffer_depth=32,
        )
        current_allocation = (1, 1)
        profile = measure_current_profile(
            current,
            current_allocation,
            hit_probabilities,
            duration=duration,
            bandwidth_bytes_per_cycle=3.0,
        )
        queue = simulate_fifo(
            build_allocation_trace(
                current, current_allocation, hit_probabilities
            ),
            3.0,
            buffer_depth=32,
        )
        calibrator = FeedbackCalibrator()
        calibrator.update(queue.mean_wait, kingman_wait(profile.stats))

        full_scores = [
            score_allocation(
                current,
                allocation,
                hit_probabilities,
                current_profile=profile,
                base_beta=calibrator.beta,
                use_feedback=True,
                model="gg1",
                **common,
            )
            for allocation in candidates
        ]
        direct_scores = [
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
        ]
        window_scores = [
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
        ]
        oracle_scores = [
            oracle_score_allocation(
                future, allocation, hit_probabilities, **common
            )
            for allocation in candidates
        ]
        full_best = max(full_scores, key=lambda score: score.objective)
        direct_best = max(direct_scores, key=lambda score: score.objective)
        window_best = max(window_scores, key=lambda score: score.objective)
        oracle_best = max(oracle_scores, key=lambda score: score.objective)
        oracle_by_allocation = {
            score.allocation: score.objective for score in oracle_scores
        }

        agreement += full_best.allocation == direct_best.allocation
        window_agreement += full_best.allocation == window_best.allocation
        full_oracle_agreement += full_best.allocation == oracle_best.allocation
        direct_oracle_agreement += (
            direct_best.allocation == oracle_best.allocation
        )
        window_oracle_agreement += (
            window_best.allocation == oracle_best.allocation
        )
        for full, direct, window_score in zip(
            full_scores, direct_scores, window_scores
        ):
            if math.isfinite(full.objective) and math.isfinite(
                direct.objective
            ):
                objective_differences.append(
                    abs(full.objective - direct.objective)
                )
            if math.isfinite(full.objective) and math.isfinite(
                window_score.objective
            ):
                window_objective_differences.append(
                    abs(full.objective - window_score.objective)
                )
        selected_objective_differences.append(
            abs(full_best.objective - direct_best.objective)
        )
        full_regrets.append(
            oracle_best.objective
            - oracle_by_allocation[full_best.allocation]
        )
        direct_regrets.append(
            oracle_best.objective
            - oracle_by_allocation[direct_best.allocation]
        )
        window_regrets.append(
            oracle_best.objective
            - oracle_by_allocation[window_best.allocation]
        )

    return {
        "seeds": seeds,
        "full_direct_agreement_pct": 100.0 * agreement / max(seeds, 1),
        "full_window_agreement_pct": (
            100.0 * window_agreement / max(seeds, 1)
        ),
        "full_oracle_agreement_pct": (
            100.0 * full_oracle_agreement / max(seeds, 1)
        ),
        "direct_oracle_agreement_pct": (
            100.0 * direct_oracle_agreement / max(seeds, 1)
        ),
        "window_oracle_agreement_pct": (
            100.0 * window_oracle_agreement / max(seeds, 1)
        ),
        "max_candidate_objective_diff": (
            max(objective_differences) if objective_differences else 0.0
        ),
        "max_selected_objective_diff": (
            max(selected_objective_differences)
            if selected_objective_differences
            else 0.0
        ),
        "max_window_candidate_objective_diff": (
            max(window_objective_differences)
            if window_objective_differences
            else 0.0
        ),
        "mean_full_oracle_regret": statistics.fmean(full_regrets),
        "mean_direct_oracle_regret": statistics.fmean(direct_regrets),
        "mean_window_oracle_regret": statistics.fmean(window_regrets),
    }


MODEL_NAMES = (
    "legacy",
    "mg1",
    "gg1",
    "gg1_feedback",
    "section14_raw",
    "section14",
    "section14_feedback",
)


def _format_table(
    title: str, rows: dict[str, dict[str, float | int]]
) -> list[str]:
    lines = [
        title,
        "model              windows invalid%   MAE  MARE(nonzero)  "
        "P95 rel  under%  under-mag",
        "------------------ ------- -------- ------ ------------- "
        "-------- ------- ----------",
    ]
    for name in MODEL_NAMES:
        row = rows[name]
        lines.append(
            f"{name:18} {int(row['windows']):7d} "
            f"{float(row['invalid_pct']):8.2f} "
            f"{float(row['mae']):6.2f} "
            f"{float(row['mare_nonzero']):13.3f} "
            f"{float(row['p95_rel_nonzero']):8.3f} "
            f"{float(row['under_rate_pct']):7.2f} "
            f"{float(row['under_mean_when_under']):10.3f}"
        )
    return lines


def _format(result: dict[str, object]) -> str:
    overall = {name: result[name] for name in MODEL_NAMES}
    lines = _format_table("overall", overall)
    by_scenario = result["by_scenario"]
    assert isinstance(by_scenario, dict)
    for scenario in ("poisson", "deterministic", "onoff", "synchronized"):
        lines.append("")
        rows = by_scenario[scenario]
        assert isinstance(rows, dict)
        lines.extend(_format_table(f"scenario={scenario}", rows))
    meta = result["meta"]
    assert isinstance(meta, dict)
    lines.append(
        f"mean CA2 raw={float(meta['mean_ca2_raw']):.3f}, "
        f"Section14 EWMA={float(meta['mean_ca2_ewma']):.3f}, "
        "max raw-equivalence error="
        f"{float(meta['max_raw_equivalence_error']):.3e}, "
        "max count-equivalence error="
        f"{float(meta['max_count_equivalence_error']):.3e}"
    )
    return "\n".join(lines)


def _format_allocation_equivalence(
    result: dict[str, float | int]
) -> str:
    return "\n".join(
        [
            "allocation equivalence",
            f"seeds={int(result['seeds'])}",
            "full/direct agreement="
            f"{float(result['full_direct_agreement_pct']):.2f}%",
            "full/window-count agreement="
            f"{float(result['full_window_agreement_pct']):.2f}%",
            "full/oracle agreement="
            f"{float(result['full_oracle_agreement_pct']):.2f}%",
            "direct/oracle agreement="
            f"{float(result['direct_oracle_agreement_pct']):.2f}%",
            "window-count/oracle agreement="
            f"{float(result['window_oracle_agreement_pct']):.2f}%",
            "max candidate objective diff="
            f"{float(result['max_candidate_objective_diff']):.3e}",
            "max selected objective diff="
            f"{float(result['max_selected_objective_diff']):.3e}",
            "max window-count candidate objective diff="
            f"{float(result['max_window_candidate_objective_diff']):.3e}",
            "mean oracle regret: full="
            f"{float(result['mean_full_oracle_regret']):.6f}, "
            "direct="
            f"{float(result['mean_direct_oracle_regret']):.6f}, "
            "window-count="
            f"{float(result['mean_window_oracle_regret']):.6f}",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--duration", type=float, default=100_000.0)
    parser.add_argument("--bandwidth", type=float, default=8.0)
    parser.add_argument("--window", type=float, default=5_000.0)
    args = parser.parse_args()
    print(_format(evaluate(**vars(args))))
    print()
    print(
        _format_allocation_equivalence(
            evaluate_allocation_equivalence(
                seeds=args.seeds, duration=args.duration
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
