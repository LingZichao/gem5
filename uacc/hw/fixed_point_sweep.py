#!/usr/bin/env python3
"""Sweep Section 14 factor and reciprocal precision on allocation decisions."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import random
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from uacc.util.uacc_gg1_sim import (  # noqa: E402
    FeedbackCalibrator,
    allocation_vectors,
    build_allocation_trace,
    candidate_packet_rates,
    candidate_rate_sums,
    kingman_wait,
    measure_current_profile,
    on_off_arrivals,
    poisson_arrivals,
    simulate_fifo,
    window_sums_from_rates,
)


@dataclass(frozen=True)
class Format:
    integer_bits: int
    fractional_bits: int
    reciprocal_bits: int

    @property
    def name(self) -> str:
        return (
            f"Q{self.integer_bits}.{self.fractional_bits}"
            f"/R{self.reciprocal_bits}"
        )


@dataclass
class Metrics:
    format: str
    seeds: int
    utilization_limit: float = 0.9
    decisions: int = 0
    agreements: int = 0
    candidate_samples: int = 0
    candidate_validity_disagreements: int = 0
    factor_saturations: int = 0
    objective_abs_error_sum: float = 0.0
    objective_abs_errors: list[float] | None = None
    max_objective_abs_error: float = 0.0
    max_selected_objective_error: float = 0.0
    min_reference_margin: float = math.inf
    first_mismatch_seed: int | None = None
    regret_sum: float = 0.0
    max_regret: float = 0.0
    mismatch_details: dict | None = None

    def __post_init__(self) -> None:
        self.objective_abs_errors = []

    def summary(self) -> dict[str, float | int | str | None]:
        errors = self.objective_abs_errors or []
        mismatches = self.decisions - self.agreements
        return {
            "format": self.format,
            "seeds": self.seeds,
            "utilization_limit": self.utilization_limit,
            "allocation_agreement_pct": (
                100.0 * self.agreements / max(self.decisions, 1)
            ),
            "candidate_objective_mae": (
                self.objective_abs_error_sum
                / max(self.candidate_samples, 1)
            ),
            "candidate_validity_disagreements": (
                self.candidate_validity_disagreements
            ),
            "candidate_objective_p99": (
                percentile(errors, 0.99) if errors else 0.0
            ),
            "max_candidate_objective_error": self.max_objective_abs_error,
            "max_selected_objective_error": self.max_selected_objective_error,
            "factor_saturations": self.factor_saturations,
            "min_reference_margin": self.min_reference_margin,
            "first_mismatch_seed": self.first_mismatch_seed,
            "mismatches": mismatches,
            "mean_oracle_regret": self.regret_sum / max(self.decisions, 1),
            "mean_mismatch_regret": (
                self.regret_sum / mismatches if mismatches else 0.0
            ),
            "max_oracle_regret": self.max_regret,
            "mismatch_details": self.mismatch_details,
        }


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(int(math.ceil(fraction * len(ordered))) - 1, len(ordered) - 1)
    return ordered[max(index, 0)]


def quantize_unsigned(value: float, fmt: Format) -> tuple[float, bool]:
    scale = 1 << fmt.fractional_bits
    maximum = (1 << fmt.integer_bits) - 1.0 / scale
    saturated = value > maximum
    clipped = min(max(value, 0.0), maximum)
    return math.floor(clipped * scale) / scale, saturated


def utilization_limit(fmt: Format | None) -> float:
    if fmt is None:
        return 0.9
    return quantize_unsigned(0.9, fmt)[0]


def reciprocal_divide(
    numerator: float, denominator: float, fractional_bits: int
) -> float:
    if numerator <= 0.0:
        return 0.0
    if denominator <= 0.0:
        return math.inf
    exponent = math.floor(math.log2(denominator))
    normalized = denominator / (2.0**exponent)
    scale = 1 << fractional_bits
    reciprocal = math.floor(scale / normalized) / scale
    return numerator * reciprocal / (2.0**exponent)


def queue_cost(
    r0: float,
    r1: float,
    r2: float,
    duration: float,
    ca2: float,
    beta: float,
    reciprocal_bits: int | None,
) -> float:
    if r0 <= 0.0 or r1 <= 0.0:
        return 0.0
    numerator = r1 * r1 * max(ca2 - 1.0, 0.0) + r0 * r2
    denominator = 2.0 * (duration - r1)
    quotient = (
        numerator / denominator
        if reciprocal_bits is None
        else reciprocal_divide(numerator, denominator, reciprocal_bits)
    )
    return beta * quotient / duration


def candidate_objectives(
    profile,
    candidates,
    hit_probabilities,
    *,
    duration: float,
    bandwidth: float,
    beta: float,
    fmt: Format | None,
) -> tuple[list[float], int]:
    if fmt is None:
        ca2 = profile.stats.ca2
        quantized_beta = beta
        reciprocal_bits = None
        rho_limit = utilization_limit(None)
        saturations = 0
    else:
        ca2, ca2_saturated = quantize_unsigned(profile.stats.ca2, fmt)
        quantized_beta, beta_saturated = quantize_unsigned(beta, fmt)
        reciprocal_bits = fmt.reciprocal_bits
        rho_limit = utilization_limit(fmt)
        saturations = int(ca2_saturated) + int(beta_saturated)

    objectives = []
    for allocation in candidates:
        rates = candidate_packet_rates(
            profile,
            allocation,
            hit_probabilities,
            bandwidth_bytes_per_cycle=bandwidth,
        )
        sums = candidate_rate_sums(rates)
        window = window_sums_from_rates(sums, duration)
        if window.r1 >= rho_limit * duration:
            objectives.append(-math.inf)
            continue
        capacity_gain = sum(
            miss_rate
            * hit_probabilities[core][allocation[core]]
            * 300.0
            for core, miss_rate in enumerate(profile.core_miss_rates)
        )
        fixed_cost = sums.l0 * 20.0
        objectives.append(
            capacity_gain
            - fixed_cost
            - queue_cost(
                window.r0,
                window.r1,
                window.r2,
                duration,
                ca2,
                quantized_beta,
                reciprocal_bits,
            )
        )
    return objectives, saturations


def evaluate(formats: list[Format], seeds: int, duration: float) -> list[dict]:
    hit_probabilities = (
        [0.0, 0.42, 0.70, 0.80],
        [0.0, 0.10, 0.23, 0.30],
    )
    candidates = list(allocation_vectors(2, 3))
    metrics = {
        fmt.name: Metrics(
            fmt.name,
            seeds,
            utilization_limit=utilization_limit(fmt),
        )
        for fmt in formats
    }

    for seed in range(seeds):
        rng = random.Random(seed)
        misses = [
            on_off_arrivals(0.14, 100.0, 160.0, duration, rng),
            poisson_arrivals(0.045, duration, rng),
        ]
        profile = measure_current_profile(
            misses,
            (1, 1),
            hit_probabilities,
            duration=duration,
            bandwidth_bytes_per_cycle=3.0,
        )
        queue = simulate_fifo(
            build_allocation_trace(misses, (1, 1), hit_probabilities),
            3.0,
            buffer_depth=32,
        )
        calibrator = FeedbackCalibrator()
        calibrator.update(queue.mean_wait, kingman_wait(profile.stats))
        beta = calibrator.beta
        reference, _ = candidate_objectives(
            profile,
            candidates,
            hit_probabilities,
            duration=duration,
            bandwidth=3.0,
            beta=beta,
            fmt=None,
        )
        valid_reference = sorted(
            (value, index)
            for index, value in enumerate(reference)
            if math.isfinite(value)
        )
        reference_best = valid_reference[-1][1]
        margin = (
            valid_reference[-1][0] - valid_reference[-2][0]
            if len(valid_reference) > 1
            else math.inf
        )

        for fmt in formats:
            current = metrics[fmt.name]
            quantized, saturations = candidate_objectives(
                profile,
                candidates,
                hit_probabilities,
                duration=duration,
                bandwidth=3.0,
                beta=beta,
                fmt=fmt,
            )
            quantized_best = max(
                range(len(quantized)), key=lambda index: quantized[index]
            )
            current.decisions += 1
            current.agreements += quantized_best == reference_best
            current.factor_saturations += saturations
            current.min_reference_margin = min(
                current.min_reference_margin, margin
            )
            if (
                quantized_best != reference_best
                and current.first_mismatch_seed is None
            ):
                current.first_mismatch_seed = seed
                current.mismatch_details = {
                    "seed": seed,
                    "ca2": profile.stats.ca2,
                    "beta": beta,
                    "reference_candidate": reference_best,
                    "quantized_candidate": quantized_best,
                    "reference_best_objective": reference[reference_best],
                    "reference_quantized_choice_objective": reference[
                        quantized_best
                    ],
                    "quantized_best_objective": quantized[quantized_best],
                    "quantized_reference_choice_objective": quantized[
                        reference_best
                    ],
                    "reference_margin": margin,
                }
            regret = max(
                0.0,
                reference[reference_best] - reference[quantized_best],
            )
            current.regret_sum += regret
            current.max_regret = max(current.max_regret, regret)
            for expected, actual in zip(reference, quantized):
                if math.isfinite(expected) != math.isfinite(actual):
                    current.candidate_validity_disagreements += 1
                if not math.isfinite(expected) or not math.isfinite(actual):
                    continue
                error = abs(expected - actual)
                current.candidate_samples += 1
                current.objective_abs_error_sum += error
                assert current.objective_abs_errors is not None
                current.objective_abs_errors.append(error)
                current.max_objective_abs_error = max(
                    current.max_objective_abs_error, error
                )
            current.max_selected_objective_error = max(
                current.max_selected_objective_error,
                abs(reference[reference_best] - quantized[reference_best]),
            )

    return [metrics[fmt.name].summary() for fmt in formats]


def format_table(rows: list[dict]) -> str:
    lines = [
        "format       agree%     MAE       P99       max  mean-regret "
        "max-regret  sat  first-mismatch",
        "------------ ------- --------- --------- --------- ----------- "
        "---------- ---- --------------",
    ]
    for row in rows:
        lines.append(
            f"{row['format']:12} {row['allocation_agreement_pct']:7.3f} "
            f"{row['candidate_objective_mae']:9.3e} "
            f"{row['candidate_objective_p99']:9.3e} "
            f"{row['max_candidate_objective_error']:9.3e} "
            f"{row['mean_oracle_regret']:11.3e} "
            f"{row['max_oracle_regret']:10.3e} "
            f"{row['factor_saturations']:4d} "
            f"{str(row['first_mismatch_seed']):>14}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=100)
    parser.add_argument("--duration", type=float, default=100_000.0)
    parser.add_argument("--json", type=Path)
    parser.add_argument(
        "--formats",
        nargs="+",
        default=("Q4.1/R16", "Q4.2/R16", "Q4.4/R16"),
        metavar="Q<I>.<F>/R<R>",
        help="fixed-point formats to evaluate (default: F1/F2/F4 with R16)",
    )
    args = parser.parse_args()
    formats = []
    for value in args.formats:
        try:
            factor, reciprocal = value.upper().split("/R", maxsplit=1)
            integer, fractional = factor.removeprefix("Q").split(".")
            formats.append(
                Format(int(integer), int(fractional), int(reciprocal))
            )
        except (TypeError, ValueError) as error:
            parser.error(f"invalid format {value!r}: expected Q<I>.<F>/R<R>")
    rows = evaluate(formats, args.seeds, args.duration)
    print(format_table(rows))
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
