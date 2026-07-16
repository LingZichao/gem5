#!/usr/bin/env python3
"""Range-aware fixed-point pre-simulation for the Section 14 allocator.

The earlier precision sweep varied only the shared fractional width.  Most of
the allocator remained 40--128 bits wide, and the utilization threshold also
lost precision with the factor format.  This model fixes CA2/beta and cost at
Q4.4, then varies the supported integer range.  It:

* derives integer widths from explicit engineering bounds;
* keeps the utilization guard precision independent from CA2/beta;
* models the HLS Q16 reciprocal, truncation, and stage saturation; and
* records decision error and the observed range of every major value.

The synthetic workload is still a pre-simulation.  Passing it selects HLS
candidates; it does not replace directed boundary tests or gem5 trace replay.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field, replace
import json
import math
from pathlib import Path
import random
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from util.uacc_gg1_sim import (  # noqa: E402
    FeedbackCalibrator,
    allocation_vectors,
    build_allocation_trace,
    kingman_wait,
    measure_current_profile,
    on_off_arrivals,
    poisson_arrivals,
    simulate_fifo,
)


RECIPROCAL_LUT_Q16 = (
    64528, 62602, 60787, 59075, 57456, 55924, 54471, 53092,
    51782, 50534, 49345, 48210, 47127, 46091, 45100, 44151,
    43240, 42367, 41528, 40721, 39946, 39199, 38480, 37787,
    37118, 36472, 35849, 35246, 34664, 34100, 33554, 33026,
)


def unsigned_bits(maximum: int) -> int:
    """Return the bits required to represent every value in [0, maximum]."""

    if maximum < 0:
        raise ValueError("unsigned maximum cannot be negative")
    return max(1, maximum.bit_length())


def signed_bits(maximum_magnitude: int) -> int:
    """Return two's-complement bits for +/- maximum_magnitude."""

    if maximum_magnitude < 0:
        raise ValueError("signed magnitude cannot be negative")
    return unsigned_bits(maximum_magnitude) + 1


def quantize_unsigned(
    value: float,
    integer_bits: int,
    fractional_bits: int,
    rounding: str,
) -> tuple[int, bool]:
    if integer_bits < 1 or fractional_bits < 0:
        raise ValueError("invalid unsigned fixed-point format")
    scale = 1 << fractional_bits
    maximum = (1 << (integer_bits + fractional_bits)) - 1
    scaled = value * scale
    if rounding == "truncate":
        quantized = math.floor(scaled)
    elif rounding == "nearest_even":
        quantized = round(scaled)
    else:
        raise ValueError(f"unsupported rounding mode: {rounding}")
    saturated = quantized > maximum or quantized < 0
    return min(max(quantized, 0), maximum), saturated


def saturate_unsigned(value: int, bits: int) -> tuple[int, bool]:
    maximum = (1 << bits) - 1
    saturated = value < 0 or value > maximum
    return min(max(value, 0), maximum), saturated


def saturate_signed(value: int, bits: int) -> tuple[int, bool]:
    minimum = -(1 << (bits - 1))
    maximum = (1 << (bits - 1)) - 1
    saturated = value < minimum or value > maximum
    return min(max(value, minimum), maximum), saturated


def reciprocal_divide_q16(numerator: int, denominator: int) -> int:
    """Bit-equivalent integer model of the HLS R16 reciprocal divider."""

    if numerator <= 0:
        return 0
    if denominator <= 0:
        raise ZeroDivisionError("reciprocal denominator must be positive")
    reciprocal_bits = 16
    exponent = denominator.bit_length() - 1
    if exponent >= reciprocal_bits:
        normalized_q = denominator >> (exponent - reciprocal_bits)
    else:
        normalized_q = denominator << (reciprocal_bits - exponent)
    lut_index = (normalized_q >> 11) & 0x1F
    reciprocal_q = RECIPROCAL_LUT_Q16[lut_index]
    product_q = normalized_q * reciprocal_q
    correction_q = (2 << reciprocal_bits) - (
        product_q >> reciprocal_bits
    )
    reciprocal_q = (
        reciprocal_q * correction_q
    ) >> reciprocal_bits
    return (numerator * reciprocal_q) >> (reciprocal_bits + exponent)


@dataclass(frozen=True)
class RangeAssumptions:
    max_window_cycles: int
    max_packets_per_domain: int
    max_domains: int = 8
    max_service_cycles: int = 32
    max_wait_cycles: int = 4096
    max_fixed_latency_cycles: int = 512
    max_gain_cycles_per_packet: int = 512
    ca2_max: int = 8
    beta_max: int = 4
    rho_numerator: int = 9
    rho_denominator: int = 10

    def __post_init__(self) -> None:
        positive = (
            self.max_window_cycles,
            self.max_packets_per_domain,
            self.max_domains,
            self.max_service_cycles,
            self.max_wait_cycles,
            self.max_fixed_latency_cycles,
            self.max_gain_cycles_per_packet,
            self.ca2_max,
            self.beta_max,
            self.rho_numerator,
            self.rho_denominator,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("all range assumptions must be positive")
        if self.rho_numerator >= self.rho_denominator:
            raise ValueError("rho maximum must be less than one")


@dataclass(frozen=True)
class DerivedWidths:
    count_bits: int
    window_bits: int
    arrival_sum_bits: int
    arrival_square_sum_bits: int
    wait_sum_bits: int
    service_bits: int
    service_square_bits: int
    busy_sum_bits: int
    service_second_sum_bits: int
    fixed_latency_integer_bits: int
    cost_integer_bits: int
    total_cost_integer_bits: int
    score_integer_bits: int
    ca2_dividend_integer_bits: int
    ca2_divisor_bits: int
    feedback_dividend_integer_bits: int
    shared_divider_integer_bits: int


def derive_widths(bounds: RangeAssumptions) -> DerivedWidths:
    """Derive safe widths for the stated bounded operating envelope."""

    window = bounds.max_window_cycles
    packets = bounds.max_packets_per_domain
    service = bounds.max_service_cycles

    # The first arrival after an idle window is explicitly assumed to be
    # clamped to one window.  Remaining deltas span at most one more window.
    arrival_sum_max = 2 * window
    arrival_square_sum_max = 2 * window * window
    wait_sum_max = packets * bounds.max_wait_cycles
    busy_sum_max = packets * service
    second_sum_max = packets * service * service

    valid_busy = min(
        busy_sum_max,
        (bounds.rho_numerator * window) // bounds.rho_denominator - 1,
    )
    valid_busy = max(valid_busy, 0)
    numerator_max = (
        valid_busy * valid_busy * (bounds.ca2_max - 1)
        + packets * second_sum_max
    )
    denominator_min = max(2 * (window - valid_busy), 1)
    model_cost_max = numerator_max // denominator_min
    adjusted_cost_max = bounds.beta_max * model_cost_max
    queue_total_max = bounds.max_domains * adjusted_cost_max
    fixed_total_max = (
        bounds.max_domains
        * packets
        * bounds.max_fixed_latency_cycles
    )
    gain_max = packets * bounds.max_gain_cycles_per_packet
    total_max = max(queue_total_max, fixed_total_max, gain_max)
    score_magnitude_max = queue_total_max + fixed_total_max + gain_max

    ca2_dividend_max = packets * arrival_square_sum_max
    ca2_divisor_max = arrival_sum_max * arrival_sum_max
    feedback_dividend_max = wait_sum_max
    shared_divider_max = max(
        ca2_dividend_max,
        ca2_divisor_max,
        feedback_dividend_max,
    )

    return DerivedWidths(
        count_bits=unsigned_bits(packets),
        window_bits=unsigned_bits(window),
        arrival_sum_bits=unsigned_bits(arrival_sum_max),
        arrival_square_sum_bits=unsigned_bits(arrival_square_sum_max),
        wait_sum_bits=unsigned_bits(wait_sum_max),
        service_bits=unsigned_bits(service),
        service_square_bits=unsigned_bits(service * service),
        busy_sum_bits=unsigned_bits(busy_sum_max),
        service_second_sum_bits=unsigned_bits(second_sum_max),
        fixed_latency_integer_bits=unsigned_bits(
            bounds.max_fixed_latency_cycles
        ),
        cost_integer_bits=unsigned_bits(adjusted_cost_max),
        total_cost_integer_bits=unsigned_bits(total_max),
        score_integer_bits=signed_bits(score_magnitude_max),
        ca2_dividend_integer_bits=unsigned_bits(ca2_dividend_max),
        ca2_divisor_bits=unsigned_bits(ca2_divisor_max),
        feedback_dividend_integer_bits=unsigned_bits(
            feedback_dividend_max
        ),
        shared_divider_integer_bits=unsigned_bits(shared_divider_max),
    )


@dataclass(frozen=True)
class SweepFormat:
    name: str
    bounds: RangeAssumptions
    fractional_bits: int
    rho_fractional_bits: int = 8
    reciprocal_bits: int = 16
    factor_integer_bits: int = 4
    rounding: str = "truncate"
    legacy_wide: bool = False
    width_overrides: tuple[tuple[str, int], ...] = ()

    @property
    def widths(self) -> DerivedWidths:
        if self.legacy_wide:
            widths = DerivedWidths(
                count_bits=32,
                window_bits=40,
                arrival_sum_bits=64,
                arrival_square_sum_bits=96,
                wait_sum_bits=64,
                service_bits=24,
                service_square_bits=48,
                busy_sum_bits=56,
                service_second_sum_bits=80,
                fixed_latency_integer_bits=40,
                cost_integer_bits=80,
                total_cost_integer_bits=84,
                score_integer_bits=85,
                ca2_dividend_integer_bits=128,
                ca2_divisor_bits=128,
                feedback_dividend_integer_bits=128,
                shared_divider_integer_bits=128,
            )
        else:
            widths = derive_widths(self.bounds)
        overrides = dict(self.width_overrides)
        if any(value < 1 for value in overrides.values()):
            raise ValueError("all width overrides must be positive")
        return replace(widths, **overrides)


@dataclass(frozen=True)
class CandidateWindow:
    allocation: tuple[int, ...]
    r0: int
    r1: int
    r2: int
    fixed_cost: int
    capacity_gain: int


@dataclass
class RangeTracker:
    maxima: dict[str, int | float] = field(default_factory=dict)

    def observe(self, **values: int | float) -> None:
        for name, value in values.items():
            current = self.maxima.get(name)
            magnitude = abs(value)
            if current is None or magnitude > current:
                self.maxima[name] = magnitude

    def required_bits(self) -> dict[str, int]:
        result = {}
        for name, value in self.maxima.items():
            if isinstance(value, float):
                integer = int(math.ceil(value))
            else:
                integer = value
            result[name] = unsigned_bits(max(integer, 0))
        return result


@dataclass
class FormatMetrics:
    format: SweepFormat
    seeds: int
    decisions: int = 0
    agreements: int = 0
    candidate_samples: int = 0
    objective_error_sum: float = 0.0
    objective_errors: list[float] = field(default_factory=list)
    regret_sum: float = 0.0
    max_regret: float = 0.0
    first_mismatch_seed: int | None = None
    comparison_format: str | None = None
    comparison_decisions: int = 0
    comparison_agreements: int = 0
    first_comparison_mismatch_seed: int | None = None
    mismatch_details: dict | None = None
    saturation_counts: dict[str, int] = field(default_factory=dict)

    def saturation(self, name: str, occurred: bool) -> None:
        if occurred:
            self.saturation_counts[name] = (
                self.saturation_counts.get(name, 0) + 1
            )

    def summary(self) -> dict:
        errors = sorted(self.objective_errors)
        p99_index = max(0, math.ceil(0.99 * len(errors)) - 1)
        mismatches = self.decisions - self.agreements
        return {
            "format": self.format.name,
            "bounds": asdict(self.format.bounds),
            "fractional_bits": self.format.fractional_bits,
            "rho_fractional_bits": self.format.rho_fractional_bits,
            "reciprocal_bits": self.format.reciprocal_bits,
            "rounding": self.format.rounding,
            "widths": asdict(self.format.widths),
            "seeds": self.seeds,
            "allocation_agreement_pct": (
                100.0 * self.agreements / max(self.decisions, 1)
            ),
            "mismatches": mismatches,
            "candidate_objective_mae": (
                self.objective_error_sum / max(self.candidate_samples, 1)
            ),
            "candidate_objective_p99": (
                errors[p99_index] if errors else 0.0
            ),
            "max_candidate_objective_error": (
                errors[-1] if errors else 0.0
            ),
            "mean_oracle_regret": (
                self.regret_sum / max(self.decisions, 1)
            ),
            "max_oracle_regret": self.max_regret,
            "first_mismatch_seed": self.first_mismatch_seed,
            "comparison_format": self.comparison_format,
            "agreement_with_comparison_pct": (
                100.0 * self.comparison_agreements
                / max(self.comparison_decisions, 1)
                if self.comparison_format is not None else None
            ),
            "first_comparison_mismatch_seed": (
                self.first_comparison_mismatch_seed
            ),
            "mismatch_details": self.mismatch_details,
            "saturation_counts": dict(sorted(
                self.saturation_counts.items()
            )),
        }


def build_candidates(
    miss_counts: list[int],
    hit_probabilities: tuple[list[float], ...],
    current_allocation: tuple[int, ...],
    *,
    bandwidth_bytes_per_cycle: float,
    duration: int,
) -> list[CandidateWindow]:
    request_service = math.ceil(8 / bandwidth_bytes_per_cycle)
    hit_service = math.ceil(64 / bandwidth_bytes_per_cycle)
    miss_service = request_service
    candidates = list(allocation_vectors(len(miss_counts), 3))
    candidates.remove(current_allocation)
    candidates.insert(0, current_allocation)
    result = []
    for allocation in candidates:
        r0 = 0
        r1 = 0
        r2 = 0
        fixed_cost = 0
        capacity_gain = 0
        for core, (misses, ways) in enumerate(zip(miss_counts, allocation)):
            if ways == 0:
                continue
            probability = hit_probabilities[core][ways]
            hits = round(misses * probability)
            misses_after_lookup = misses - hits
            class_counts = (misses, hits, misses_after_lookup)
            services = (request_service, hit_service, miss_service)
            for count, service in zip(class_counts, services):
                r0 += count
                r1 += count * service
                r2 += count * service * service
                fixed_cost += count * 20
            capacity_gain += round(misses * probability * 300)
        result.append(CandidateWindow(
            allocation=allocation,
            r0=r0,
            r1=r1,
            r2=r2,
            fixed_cost=fixed_cost,
            capacity_gain=capacity_gain,
        ))
    return result


def reference_objective(
    candidate: CandidateWindow,
    duration: int,
    ca2: float,
    beta: float,
    rho_max: float = 0.9,
) -> float:
    if candidate.r1 >= rho_max * duration:
        return -math.inf
    effective_ca2 = max(1.0, ca2)
    numerator = (
        candidate.r1 * candidate.r1 * (effective_ca2 - 1.0)
        + candidate.r0 * candidate.r2
    )
    denominator = 2 * (duration - candidate.r1)
    queue_cost = beta * numerator / denominator
    return candidate.capacity_gain - candidate.fixed_cost - queue_cost


def fixed_objective(
    candidate: CandidateWindow,
    duration: int,
    ca2: float,
    beta: float,
    fmt: SweepFormat,
    metrics: FormatMetrics,
) -> float:
    widths = fmt.widths
    fractional_bits = fmt.fractional_bits
    scale = 1 << fractional_bits

    window, saturated = saturate_unsigned(duration, widths.window_bits)
    metrics.saturation("window", saturated)
    if window == 0:
        return -math.inf

    r0, r0_saturated = saturate_unsigned(
        candidate.r0, widths.count_bits
    )
    metrics.saturation("r0", r0_saturated)
    r1, r1_saturated = saturate_unsigned(
        candidate.r1, widths.busy_sum_bits
    )
    metrics.saturation("r1", r1_saturated)
    r2, r2_saturated = saturate_unsigned(
        candidate.r2, widths.service_second_sum_bits
    )
    metrics.saturation("r2", r2_saturated)
    if r0_saturated or r1_saturated or r2_saturated:
        return -math.inf

    rho_q, saturated = quantize_unsigned(
        0.9, 1, fmt.rho_fractional_bits, fmt.rounding
    )
    metrics.saturation("rho", saturated)
    if (
        r1 * (1 << fmt.rho_fractional_bits)
        >= window * rho_q
    ):
        return -math.inf

    ca2_q, saturated = quantize_unsigned(
        ca2,
        fmt.factor_integer_bits,
        fractional_bits,
        fmt.rounding,
    )
    metrics.saturation("ca2", saturated)
    beta_q, saturated = quantize_unsigned(
        beta,
        fmt.factor_integer_bits,
        fractional_bits,
        fmt.rounding,
    )
    metrics.saturation("beta", saturated)
    one_q = scale
    effective_ca2_q = max(one_q, ca2_q)
    numerator_q = (
        r1 * r1 * (effective_ca2_q - one_q)
        + r0 * r2 * scale
    )
    denominator = 2 * (window - r1)
    if fmt.reciprocal_bits != 16:
        raise ValueError("only the implemented R16 divider is supported")
    model_cost_q = reciprocal_divide_q16(numerator_q, denominator)
    model_cost_q, model_saturated = saturate_unsigned(
        model_cost_q, widths.cost_integer_bits + fractional_bits
    )
    metrics.saturation("model_cost", model_saturated)
    adjusted_q = (model_cost_q * beta_q) >> fractional_bits
    adjusted_q, adjusted_saturated = saturate_unsigned(
        adjusted_q, widths.cost_integer_bits + fractional_bits
    )
    metrics.saturation("adjusted_cost", adjusted_saturated)
    if model_saturated or adjusted_saturated:
        return -math.inf

    total_bits = widths.total_cost_integer_bits + fractional_bits
    fixed_q, fixed_saturated = saturate_unsigned(
        candidate.fixed_cost * scale, total_bits
    )
    metrics.saturation("fixed_cost", fixed_saturated)
    gain_q, gain_saturated = saturate_unsigned(
        candidate.capacity_gain * scale, total_bits
    )
    metrics.saturation("capacity_gain", gain_saturated)
    queue_q, queue_saturated = saturate_unsigned(adjusted_q, total_bits)
    metrics.saturation("queue_total", queue_saturated)
    if fixed_saturated or queue_saturated:
        return -math.inf
    objective_q = gain_q - fixed_q - queue_q
    objective_q, saturated = saturate_signed(
        objective_q, widths.score_integer_bits + fractional_bits
    )
    metrics.saturation("score", saturated)
    return objective_q / scale


def evaluate(
    formats: list[SweepFormat],
    seeds: int,
    duration: int,
    workload: str = "baseline",
    comparison_format: str | None = None,
) -> tuple[list[dict], dict]:
    hit_probabilities = (
        [0.0, 0.42, 0.70, 0.80],
        [0.0, 0.10, 0.23, 0.30],
    )
    current_allocation = (1, 1)
    metrics = {
        fmt.name: FormatMetrics(fmt, seeds) for fmt in formats
    }
    if comparison_format is not None:
        if comparison_format not in metrics:
            raise ValueError("comparison format must be part of the sweep")
        for current in metrics.values():
            current.comparison_format = comparison_format
    ranges = RangeTracker()

    for seed in range(seeds):
        if workload == "baseline":
            on_rate = 0.14
            on_mean = 100.0
            off_mean = 160.0
            poisson_rate = 0.045
            bandwidth = 3.0
        elif workload == "diverse":
            configuration = random.Random(seed ^ 0x51A7)
            on_rate = configuration.uniform(0.03, 0.24)
            on_mean = configuration.uniform(50.0, 250.0)
            off_mean = configuration.uniform(60.0, 320.0)
            poisson_rate = configuration.uniform(0.008, 0.13)
            bandwidth = configuration.choice((2.0, 3.0, 4.0, 6.0, 8.0))
        else:
            raise ValueError(f"unsupported workload mode: {workload}")
        rng = random.Random(seed)
        misses = [
            on_off_arrivals(on_rate, on_mean, off_mean, duration, rng),
            poisson_arrivals(poisson_rate, duration, rng),
        ]
        profile = measure_current_profile(
            misses,
            current_allocation,
            hit_probabilities,
            duration=duration,
            bandwidth_bytes_per_cycle=bandwidth,
        )
        queue = simulate_fifo(
            build_allocation_trace(
                misses, current_allocation, hit_probabilities
            ),
            bandwidth,
            buffer_depth=32,
        )
        calibrator = FeedbackCalibrator(alpha=0.25)
        calibrator.update(queue.mean_wait, kingman_wait(profile.stats))
        beta = calibrator.beta
        candidates = build_candidates(
            [len(stream) for stream in misses],
            hit_probabilities,
            current_allocation,
            bandwidth_bytes_per_cycle=bandwidth,
            duration=duration,
        )
        reference = [
            reference_objective(
                candidate, duration, profile.stats.ca2, beta
            )
            for candidate in candidates
        ]
        reference_best = max(
            range(len(reference)), key=lambda index: reference[index]
        )
        ranges.observe(ca2=profile.stats.ca2, beta=beta)
        for candidate, objective in zip(candidates, reference):
            ranges.observe(
                r0=candidate.r0,
                r1=candidate.r1,
                r2=candidate.r2,
                fixed_cost=candidate.fixed_cost,
                capacity_gain=candidate.capacity_gain,
            )
            if math.isfinite(objective):
                ranges.observe(reference_objective=objective)

        fixed_by_format = {}
        best_by_format = {}
        for fmt in formats:
            current = metrics[fmt.name]
            fixed = [
                fixed_objective(
                    candidate,
                    duration,
                    profile.stats.ca2,
                    beta,
                    fmt,
                    current,
                )
                for candidate in candidates
            ]
            fixed_by_format[fmt.name] = fixed
            best_by_format[fmt.name] = max(
                range(len(fixed)), key=lambda index: fixed[index]
            )

        for fmt in formats:
            current = metrics[fmt.name]
            fixed = fixed_by_format[fmt.name]
            fixed_best = best_by_format[fmt.name]
            current.decisions += 1
            if fixed_best == reference_best:
                current.agreements += 1
            elif current.first_mismatch_seed is None:
                current.first_mismatch_seed = seed
            if comparison_format is not None:
                comparison_best = best_by_format[comparison_format]
                current.comparison_decisions += 1
                if fixed_best == comparison_best:
                    current.comparison_agreements += 1
                elif current.first_comparison_mismatch_seed is None:
                    current.first_comparison_mismatch_seed = seed
                    selected = candidates[fixed_best]
                    expected = candidates[comparison_best]
                    current.mismatch_details = {
                        "seed": seed,
                        "workload": {
                            "on_rate": on_rate,
                            "on_mean": on_mean,
                            "off_mean": off_mean,
                            "poisson_rate": poisson_rate,
                            "bandwidth_bytes_per_cycle": bandwidth,
                        },
                        "comparison_candidate": comparison_best,
                        "comparison_allocation": list(expected.allocation),
                        "selected_candidate": fixed_best,
                        "selected_allocation": list(selected.allocation),
                        "comparison_candidate_sums": {
                            "r0": expected.r0,
                            "r1": expected.r1,
                            "r2": expected.r2,
                            "fixed_cost": expected.fixed_cost,
                            "capacity_gain": expected.capacity_gain,
                        },
                        "oracle_candidate": reference_best,
                        "oracle_objective_for_comparison": (
                            reference[comparison_best]
                        ),
                        "oracle_objective_for_selected": (
                            reference[fixed_best]
                        ),
                        "oracle_regret": max(
                            0.0,
                            reference[reference_best] - reference[fixed_best],
                        ),
                    }
            regret = max(
                0.0, reference[reference_best] - reference[fixed_best]
            )
            current.regret_sum += regret
            current.max_regret = max(current.max_regret, regret)
            for expected, actual in zip(reference, fixed):
                if not math.isfinite(expected) or not math.isfinite(actual):
                    continue
                error = abs(expected - actual)
                current.candidate_samples += 1
                current.objective_error_sum += error
                current.objective_errors.append(error)

    return (
        [metrics[fmt.name].summary() for fmt in formats],
        {
            "observed_maxima": ranges.maxima,
            "observed_required_integer_bits": ranges.required_bits(),
        },
    )


def default_formats() -> list[SweepFormat]:
    bounds_10k = RangeAssumptions(10_000, 10_000)
    bounds_50k = RangeAssumptions(50_000, 50_000)
    bounds_100k = RangeAssumptions(100_000, 100_000)
    bounds_200k = RangeAssumptions(200_000, 200_000)
    bounds_1m = RangeAssumptions(1_000_000, 1_000_000)
    return [
        SweepFormat("W10K-Q4.4-R16-trunc", bounds_10k, 4),
        SweepFormat("W50K-Q4.4-R16-trunc", bounds_50k, 4),
        SweepFormat("W100K-Q4.4-R16-trunc", bounds_100k, 4),
        SweepFormat("W200K-Q4.4-R16-trunc", bounds_200k, 4),
        SweepFormat("W1M-Q4.4-R16-trunc", bounds_1m, 4),
        SweepFormat(
            "legacy-wide-Q4.4-R16-trunc",
            bounds_1m,
            4,
            legacy_wide=True,
        ),
    ]


def implementation_formats() -> list[SweepFormat]:
    bounds_100k = RangeAssumptions(100_000, 100_000)
    bounds_1m = RangeAssumptions(1_000_000, 1_000_000)
    return [
        SweepFormat(
            "Lossy16-Q4.4-R16-trunc",
            bounds_100k,
            4,
            width_overrides=(
                ("busy_sum_bits", 16),
                ("service_second_sum_bits", 20),
                ("cost_integer_bits", 20),
                ("total_cost_integer_bits", 23),
                ("score_integer_bits", 24),
            ),
        ),
        SweepFormat("W100K-Q4.4-R16-trunc", bounds_100k, 4),
        SweepFormat("W1M-Q4.4-R16-trunc", bounds_1m, 4),
        SweepFormat(
            "legacy-wide-Q4.4-R16-trunc",
            bounds_1m,
            4,
            legacy_wide=True,
        ),
    ]


def format_table(rows: list[dict]) -> str:
    lines = [
        "format                         oracle%   safe%  mismatch       "
        "MAE       P99  max-regret  saturations",
        "----------------------------- ------- ------- --------- "
        "--------- --------- ----------- -----------",
    ]
    for row in rows:
        saturation_total = sum(row["saturation_counts"].values())
        comparison = row["agreement_with_comparison_pct"]
        comparison_text = (
            f"{comparison:7.3f}" if comparison is not None else "      -"
        )
        lines.append(
            f"{row['format']:29} "
            f"{row['allocation_agreement_pct']:7.3f} "
            f"{comparison_text} "
            f"{row['mismatches']:9d} "
            f"{row['candidate_objective_mae']:9.3e} "
            f"{row['candidate_objective_p99']:9.3e} "
            f"{row['max_oracle_regret']:11.3e} "
            f"{saturation_total:11d}"
        )
    return "\n".join(lines)


def implementation_profile_definitions() -> dict:
    return {
        "controlled_constants": {
            "factor_and_cost_fractional_format": "Q4.4",
            "utilization_format": "Q1.8",
            "reciprocal": "R16",
            "algorithm": "unchanged",
            "physical_constraints": "unchanged",
        },
        "primary_width_variables": {
            "count_and_window": ["count_bits", "window_bits"],
            "traffic_sums": [
                "busy_sum_bits",
                "service_second_sum_bits",
            ],
            "cost_chain": [
                "cost_integer_bits",
                "total_cost_integer_bits",
                "score_integer_bits",
            ],
            "shared_divider": ["shared_divider_integer_bits"],
        },
        "profiles": {
            "Lossy16-Q4.4-R16-trunc": {
                "meaning": (
                    "Deliberately unsafe 100K failure point; only R1/R2 "
                    "and the cost-to-score chain are aggressively narrowed."
                ),
                "supported": False,
                "note": (
                    "Lossy16 is not a uniform 16-bit design: count/window "
                    "are 17 bits, R2 is 20 bits, and the divider is 51 bits."
                ),
            },
            "W100K-Q4.4-R16-trunc": {
                "meaning": (
                    "Coherent minimum-range implementation for at most "
                    "100K cycles and packets per domain."
                ),
                "supported": True,
                "note": "Recommended for the current 100K profiling window.",
            },
            "W1M-Q4.4-R16-trunc": {
                "meaning": (
                    "Coherent conservative implementation for at most "
                    "1M cycles and packets per domain."
                ),
                "supported": True,
                "note": (
                    "Evaluated with the same 100K workload; W1M denotes "
                    "supported range, not the measurement duration."
                ),
            },
            "legacy-wide-Q4.4-R16-trunc": {
                "meaning": (
                    "Original over-wide prototype types retained as the "
                    "controlled area baseline."
                ),
                "supported": True,
                "note": "Uses the same Q4.4/Q1.8/R16 algorithm.",
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=1000)
    parser.add_argument("--duration", type=int, default=100_000)
    parser.add_argument("--json", type=Path)
    parser.add_argument(
        "--workload",
        choices=("baseline", "diverse"),
        default="baseline",
    )
    parser.add_argument(
        "--profiles",
        choices=("all", "implementation"),
        default="all",
    )
    args = parser.parse_args()
    if args.seeds <= 0 or args.duration <= 0:
        parser.error("seeds and duration must be positive")
    formats = (
        implementation_formats()
        if args.profiles == "implementation" else default_formats()
    )
    comparison_format = (
        "W100K-Q4.4-R16-trunc"
        if args.profiles == "implementation" else None
    )
    rows, ranges = evaluate(
        formats,
        args.seeds,
        args.duration,
        workload=args.workload,
        comparison_format=comparison_format,
    )
    print(format_table(rows))
    print("\nObserved maxima:")
    for name, value in sorted(ranges["observed_maxima"].items()):
        bits = ranges["observed_required_integer_bits"][name]
        print(f"  {name:20} {value:14.6g}  bits={bits}")
    if args.json:
        payload = {
            "experiment": {
                "seeds": args.seeds,
                "duration": args.duration,
                "fixed_format": "Q4.4",
                "rho_precision_is_independent": True,
                "reciprocal": "HLS Q16 LUT plus one Newton step",
                "workload": args.workload,
                "profiles": args.profiles,
            },
        }
        if args.profiles == "implementation":
            payload["profile_definitions"] = (
                implementation_profile_definitions()
            )
        payload["results"] = rows
        payload["ranges"] = ranges
        args.json.write_text(json.dumps(payload, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
