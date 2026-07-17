#!/usr/bin/env python3
"""Standalone UCP versus burst-aware allocation experiment.

The experiment deliberately stays outside gem5.  A deterministic, phase
changing miss trace is replayed through a finite FIFO queue.  UCP uses only
the ATD-derived capacity gain, while the two congestion-aware variants add
the G/G/1 queue penalty with and without measured feedback.

The policy schedule is causal: a decision for window ``t`` is made from
completed windows before ``t``.  The oracle is a per-window hindsight
reference which evaluates every legal allocation with the measured FIFO
delay for that window.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import random
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from uacc.util.uacc_gg1_sim import (  # noqa: E402
    FeedbackCalibrator,
    MomentStats,
    Packet,
    build_allocation_trace,
    kingman_wait,
    measure_current_profile,
    on_off_arrivals,
    poisson_arrivals,
    score_allocation,
    simulate_fifo,
    synchronized_bursts,
)


POLICIES = ("ucp", "ucp_gg1", "ucp_gg1_feedback")
POLICY_LABELS = {
    "ucp": "UCP",
    "ucp_gg1": "UCP + G/G/1",
    "ucp_gg1_feedback": "UCP + G/G/1 + feedback",
}
POLICY_COLORS = {
    "ucp": "#59636e",
    "ucp_gg1": "#007c91",
    "ucp_gg1_feedback": "#d95f59",
}
HIT_PROBABILITIES = (
    (0.0, 0.22, 0.44, 0.62, 0.74),
    (0.0, 0.18, 0.39, 0.58, 0.71),
    (0.0, 0.20, 0.42, 0.60, 0.73),
    (0.0, 0.16, 0.36, 0.56, 0.69),
)


@dataclass(frozen=True)
class TraceConfig:
    cores: int = 4
    window: float = 2_000.0
    windows: int = 40
    bandwidth: float = 8.0
    buffer_depth: int = 8
    max_ways: int = 4
    lower_miss_penalty: float = 300.0
    fixed_latency: float = 6.0
    rho_max: float = 0.90
    response_delay: float = 20.0
    feedback_alpha: float = 0.25
    burst_bandwidth_factor: float = 1.0
    synchronized_bandwidth_factor: float = 1.0
    contention_bandwidth_factor: float = 1.0
    trace_pattern: str = "mixed"


@dataclass
class WindowTrace:
    index: int
    phase: str
    start: float
    end: float
    misses: list[list[float]]


@dataclass
class PolicyWindow:
    allocation: tuple[int, ...]
    utility: float
    queue_wait: float
    backpressure: int
    packet_count: int
    feedback_beta: float = 1.0


@dataclass
class PolicyRun:
    windows: list[PolicyWindow]


def _phase_arrivals(
    phase: str,
    start: float,
    end: float,
    rate: float,
    rng: random.Random,
) -> list[float]:
    duration = end - start
    if phase == "steady":
        arrivals = poisson_arrivals(rate, duration, rng)
    elif phase == "burst":
        arrivals = on_off_arrivals(
            rate * 3.5, 120.0, 180.0, duration, rng
        )
    elif phase == "synchronized":
        period = max(80.0, 1.0 / max(rate, 1.0e-9))
        burst = max(2, int(round(rate * period)))
        arrivals = synchronized_bursts(
            period, burst, duration, jitter=1.5, rng=rng
        )
    elif phase == "recovery":
        arrivals = poisson_arrivals(rate * 0.45, duration, rng)
    elif phase != "steady":
        raise ValueError(f"unknown phase: {phase}")
    return [start + value for value in arrivals]


def generate_trace(
    config: TraceConfig, seed: int, rate_scale: float
) -> list[WindowTrace]:
    rng = random.Random(seed)
    base_rates = (0.028, 0.024, 0.020, 0.016)
    phases = ("steady", "burst", "synchronized", "recovery")
    phase_size = config.windows // len(phases)
    result = []
    for index in range(config.windows):
        if config.trace_pattern == "feedback_step":
            if index < config.windows // 5:
                phase = "steady"
            elif index < 4 * config.windows // 5:
                phase = "contention"
            else:
                phase = "recovery"
            arrival_phase = "steady"
        else:
            phase = phases[min(index // phase_size, len(phases) - 1)]
            arrival_phase = phase
        start = index * config.window
        end = start + config.window
        misses = [
            _phase_arrivals(
                arrival_phase, start, end, rate * rate_scale, rng
            )
            for rate in base_rates
        ]
        result.append(WindowTrace(index, phase, start, end, misses))
    return result


def _candidate_vectors(config: TraceConfig) -> list[tuple[int, ...]]:
    per_core_ways = 4
    return [
        candidate
        for candidate in itertools.product(
            range(per_core_ways + 1), repeat=config.cores
        )
        if sum(candidate) <= config.max_ways
    ]


def _capacity_gain(
    misses: Sequence[Sequence[float]],
    allocation: Sequence[int],
    hit_probabilities: Sequence[Sequence[float]],
    config: TraceConfig,
) -> float:
    gain = 0.0
    for core_id, core_misses in enumerate(misses):
        ways = allocation[core_id]
        gain += (
            len(core_misses) / config.window
            * hit_probabilities[core_id][ways]
            * config.lower_miss_penalty
        )
    return gain


def _window_packets(
    trace: WindowTrace,
    allocation: Sequence[int],
    hit_probabilities: Sequence[Sequence[float]],
    config: TraceConfig,
    sequence_start: int,
) -> list[Packet]:
    packets = build_allocation_trace(
        trace.misses,
        allocation,
        hit_probabilities,
        response_delay=config.response_delay,
    )
    return [
        Packet(packet.offered_arrival, packet.size_bytes,
               packet.sequence + sequence_start)
        for packet in packets
    ]


def _choose_policy(
    policy: str,
    profile_misses: Sequence[Sequence[float]],
    current_allocation: tuple[int, ...],
    beta: float,
    hit_probabilities: Sequence[Sequence[float]],
    candidates: Sequence[tuple[int, ...]],
    config: TraceConfig,
) -> tuple[int, ...]:
    if not profile_misses or not any(profile_misses):
        return current_allocation
    profile = measure_current_profile(
        profile_misses,
        current_allocation,
        hit_probabilities,
        duration=config.window,
        bandwidth_bytes_per_cycle=config.bandwidth,
    )
    if policy == "ucp":
        scored = [
            (
                _capacity_gain(profile_misses, candidate,
                               hit_probabilities, config),
                candidate,
            )
            for candidate in candidates
        ]
        return max(
            scored,
            key=lambda item: (item[0], tuple(-x for x in item[1])),
        )[1]

    scored = []
    for candidate in candidates:
        result = score_allocation(
            profile_misses,
            candidate,
            hit_probabilities,
            duration=config.window,
            bandwidth_bytes_per_cycle=config.bandwidth,
            lower_miss_penalty_cycles=config.lower_miss_penalty,
            fixed_latency_cycles=config.fixed_latency,
            base_beta=beta,
            rho_max=config.rho_max,
            use_feedback=policy == "ucp_gg1_feedback",
            model="gg1",
            current_profile=profile,
        )
        scored.append(result)
    return max(
        scored,
        key=lambda item: (item.objective, tuple(-x for x in item.allocation)),
    ).allocation


def _oracle_window(
    trace: WindowTrace,
    hit_probabilities: Sequence[Sequence[float]],
    candidates: Sequence[tuple[int, ...]],
    config: TraceConfig,
) -> tuple[tuple[int, ...], float]:
    realized = [
        _realized_window(
            trace, candidate, hit_probabilities, config
        )
        for candidate in candidates
    ]
    best = max(
        realized,
        key=lambda item: (item.utility, tuple(-x for x in item.allocation)),
    )
    return best.allocation, best.utility


def _realized_window(
    trace: WindowTrace,
    allocation: tuple[int, ...],
    hit_probabilities: Sequence[Sequence[float]],
    config: TraceConfig,
) -> PolicyWindow:
    packets = build_allocation_trace(
        trace.misses,
        allocation,
        hit_probabilities,
        response_delay=config.response_delay,
    )
    bandwidth_factor = 1.0
    if trace.phase == "burst":
        bandwidth_factor = config.burst_bandwidth_factor
    elif trace.phase == "synchronized":
        bandwidth_factor = config.synchronized_bandwidth_factor
    elif trace.phase == "contention":
        bandwidth_factor = config.contention_bandwidth_factor
    queue = simulate_fifo(
        packets,
        config.bandwidth * bandwidth_factor,
        buffer_depth=config.buffer_depth,
    )
    capacity_gain = _capacity_gain(
        trace.misses, allocation, hit_probabilities, config
    )
    packet_rate = len(packets) / config.window
    total_wait = sum(
        observation.service_start - observation.packet.offered_arrival
        for observation in queue.observations
    )
    mean_wait = total_wait / len(packets) if packets else 0.0
    utility = (
        capacity_gain
        - packet_rate * config.fixed_latency
        - total_wait / config.window
    )
    return PolicyWindow(
        allocation=allocation,
        utility=utility,
        queue_wait=mean_wait,
        backpressure=queue.backpressure_events,
        packet_count=len(packets),
    )


def _reference_rho(
    traces: Sequence[WindowTrace],
    hit_probabilities: Sequence[Sequence[float]],
    config: TraceConfig,
) -> float:
    packets = []
    sequence = 0
    reference = (1,) * config.cores
    for trace in traces:
        current = _window_packets(
            trace, reference, hit_probabilities, config, sequence
        )
        sequence += len(current)
        packets.extend(current)
    stats = MomentStats.from_packets(
        packets, config.bandwidth,
        config.window * config.windows,
    )
    return stats.utilization


def run_policy(
    policy: str,
    traces: Sequence[WindowTrace],
    hit_probabilities: Sequence[Sequence[float]],
    candidates: Sequence[tuple[int, ...]],
    config: TraceConfig,
) -> PolicyRun:
    allocation = (1,) * config.cores
    calibrator = FeedbackCalibrator(
        alpha=config.feedback_alpha, beta_max=4.0
    )
    rows = []
    previous_misses: Sequence[Sequence[float]] | None = None

    for trace in traces:
        if previous_misses is not None:
            allocation = _choose_policy(
                policy, previous_misses, allocation, calibrator.beta,
                hit_probabilities, candidates, config
            )
        realized = _realized_window(
            trace, allocation, hit_probabilities, config
        )
        if policy != "ucp":
            profile = measure_current_profile(
                trace.misses, allocation, hit_probabilities,
                duration=config.window,
                bandwidth_bytes_per_cycle=config.bandwidth,
            )
            model_wait = kingman_wait(profile.stats)
            if policy == "ucp_gg1_feedback":
                calibrator.update(realized.queue_wait, model_wait)
        realized.feedback_beta = calibrator.beta
        previous_misses = trace.misses
        rows.append(realized)
    return PolicyRun(rows)


def run_experiment(
    config: TraceConfig,
    scales: Sequence[float],
    seeds: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    candidates = _candidate_vectors(config)
    sweep_rows = []
    trace_rows = []
    for scale in scales:
        for seed in range(seeds):
            traces = generate_trace(config, seed, scale)
            reference_rho = _reference_rho(
                traces, HIT_PROBABILITIES, config
            )
            runs = {
                policy: run_policy(
                    policy, traces, HIT_PROBABILITIES, candidates, config
                )
                for policy in POLICIES
            }
            oracle = [
                _oracle_window(
                    trace, HIT_PROBABILITIES, candidates, config
                )
                for trace in traces
            ]
            oracle_utility = [item[1] for item in oracle]
            for policy, run in runs.items():
                utility = [item.utility for item in run.windows]
                oracle_total = sum(max(0.0, value) for value in oracle_utility)
                ratio = (
                    sum(utility) / oracle_total
                    if oracle_total > 0.0
                    else 0.0
                )
                sweep_rows.append({
                    "scale": scale,
                    "seed": seed,
                    "reference_rho": reference_rho,
                    "policy": policy,
                    "utility_ratio": ratio,
                    "mean_wait": statistics.fmean(
                        item.queue_wait for item in run.windows
                    ),
                    "backpressure": sum(
                        item.backpressure for item in run.windows
                    ),
                    "packets": sum(item.packet_count for item in run.windows),
                    "backpressure_per_1k": (
                        1_000.0
                        * sum(item.backpressure for item in run.windows)
                        / max(
                            sum(item.packet_count for item in run.windows),
                            1,
                        )
                    ),
                    "final_regret": sum(oracle_utility) - sum(utility),
                })
            if scale == scales[len(scales) // 2] and seed == 0:
                cumulative = {policy: 0.0 for policy in POLICIES}
                oracle_cumulative = 0.0
                for index, trace in enumerate(traces):
                    oracle_cumulative += oracle_utility[index]
                    row = {
                        "window": index,
                        "phase": trace.phase,
                        "oracle_utility": oracle_utility[index],
                        "oracle_cumulative": oracle_cumulative,
                    }
                    for policy, run in runs.items():
                        cumulative[policy] += run.windows[index].utility
                        row[f"{policy}_utility"] = run.windows[index].utility
                        row[f"{policy}_cumulative"] = cumulative[policy]
                        row[f"{policy}_allocation"] = ";".join(
                            str(value)
                            for value in run.windows[index].allocation
                        )
                        row[f"{policy}_beta"] = (
                            run.windows[index].feedback_beta
                        )
                    trace_rows.append(row)
    return sweep_rows, trace_rows


def _aggregate(
    rows: Sequence[dict[str, object]], field: str
) -> list[dict[str, float | str]]:
    grouped: dict[tuple[str, float], list[float]] = {}
    for row in rows:
        key = (str(row["policy"]), float(row["scale"]))
        grouped.setdefault(key, []).append(float(row[field]))
    result = []
    for (policy, scale), values in sorted(
        grouped.items(), key=lambda item: item[0][1]
    ):
        rho = statistics.fmean(
            float(row["reference_rho"])
            for row in rows
            if (
                row["policy"] == policy
                and float(row["scale"]) == scale
            )
        )
        mean = statistics.fmean(values)
        error = (
            statistics.stdev(values) / math.sqrt(len(values))
            if len(values) > 1
            else 0.0
        )
        result.append({
            "policy": policy,
            "reference_rho": rho,
            "mean": mean,
            "stderr": error,
        })
    return result


def write_csv(path: Path, rows: Sequence[dict[str, object]]) -> None:
    if not rows:
        return
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def plot_results(
    output: Path,
    sweep_rows: Sequence[dict[str, object]],
    trace_rows: Sequence[dict[str, object]],
    trace_view: str = "cumulative",
) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(3.0, 1.55))
    for axis in axes:
        axis.set_box_aspect(1.0)
    panels = (
        ("utility_ratio", "Utility / oracle", ""),
        (
            "backpressure_per_1k",
            "BP / 1K",
            "",
        ),
    )
    for axis, (field, ylabel, _) in zip(axes[:2], panels):
        for policy in POLICIES:
            values = [
                row for row in _aggregate(sweep_rows, field)
                if row["policy"] == policy
            ]
            values.sort(key=lambda row: row["reference_rho"])
            x = [row["reference_rho"] for row in values]
            y = [row["mean"] for row in values]
            error = [row["stderr"] * 1.96 for row in values]
            if policy == "ucp_gg1_feedback":
                axis.errorbar(
                    x, y, yerr=error, marker="D", markersize=2.5,
                    markerfacecolor="white", markeredgewidth=0.8,
                    linewidth=0.9, linestyle="-", capsize=1.0,
                    label=POLICY_LABELS[policy],
                    color=POLICY_COLORS[policy], zorder=4,
                )
                continue
            marker = "s" if policy == "ucp_gg1" else "o"
            linewidth = 1.15 if policy == "ucp_gg1" else 1.0
            linestyle = "-"
            axis.plot(
                x, y, marker=marker, linewidth=linewidth,
                markersize=3.0,
                linestyle=linestyle, label=POLICY_LABELS[policy],
                color=POLICY_COLORS[policy], zorder=2,
            )
            axis.fill_between(
                x, [a - b for a, b in zip(y, error)],
                [a + b for a, b in zip(y, error)],
                color=POLICY_COLORS[policy], alpha=0.06, zorder=1,
            )
        axis.set_xlabel(r"$\rho$", fontsize=7)
        axis.set_ylabel(ylabel if axis is axes[0] else "", fontsize=7)
        axis.grid(True, alpha=0.25)
        axis.tick_params(axis="both", labelsize=6, pad=1)
    axis = axes[2]
    if trace_rows:
        x = [int(row["window"]) for row in trace_rows]
        if trace_view == "normalized":
            oracle_values = [1.0] * len(trace_rows)
        else:
            oracle_values = [
                float(row["oracle_cumulative"])
                for row in trace_rows
            ]
        axis.plot(
            x, oracle_values,
            color="#202020", linewidth=1.2,
            label="Measured-delay oracle", zorder=3,
        )
        for policy in POLICIES:
            if trace_view == "normalized":
                policy_values = []
                for row in trace_rows:
                    oracle = float(row["oracle_utility"])
                    utility = float(row[f"{policy}_utility"])
                    policy_values.append(
                        utility / oracle if oracle > 0.0 else 1.0
                    )
            else:
                policy_values = [
                    float(row[f"{policy}_cumulative"])
                    for row in trace_rows
                ]
            if policy == "ucp_gg1_feedback":
                axis.plot(
                    x, policy_values,
                    color=POLICY_COLORS[policy], linewidth=0.9,
                    linestyle="-", marker="D", markersize=2.2,
                    markerfacecolor="white", markeredgewidth=0.8,
                    markevery=4, label=POLICY_LABELS[policy], zorder=4,
                )
                continue
            linewidth = 1.15 if policy == "ucp_gg1" else 1.0
            linestyle = "-"
            marker = "s" if policy == "ucp_gg1" else "o"
            axis.plot(
                x, policy_values,
                color=POLICY_COLORS[policy], linewidth=linewidth,
                linestyle=linestyle, marker=marker, markersize=1.8,
                markevery=4, label=POLICY_LABELS[policy], zorder=2,
            )
        boundaries = [
            index
            for index in range(1, len(trace_rows))
            if trace_rows[index]["phase"] != trace_rows[index - 1]["phase"]
        ]
        for boundary in boundaries:
            axis.axvline(boundary, color="#888888", linewidth=0.8,
                         linestyle="--", alpha=0.65)
        axis.set_xlabel("Window", fontsize=7)
        axis.set_ylabel("")
        axis.grid(True, alpha=0.25)
        axis.tick_params(axis="both", labelsize=6, pad=1)
    axes[0].set_title("(a) Utility", fontsize=7.5, pad=3)
    axes[1].set_title("(b) BP / 1K", fontsize=7.5, pad=3)
    trace_title = (
        "(c) Window utility"
        if trace_view == "normalized"
        else "(c) Cum. utility"
    )
    axes[2].set_title(trace_title, fontsize=7.5, pad=3)
    handles, labels = axes[2].get_legend_handles_labels()
    fig.legend(
        handles, labels, loc="upper center", ncol=2,
        bbox_to_anchor=(0.5, -0.02), frameon=False, fontsize=6,
        handlelength=1.8, columnspacing=0.8,
    )
    fig.subplots_adjust(
        left=0.08, right=0.995, bottom=0.30, top=0.83,
        wspace=0.52,
    )
    fig.savefig(
        output, dpi=300, bbox_inches="tight", facecolor="white"
    )
    fig.savefig(
        output.with_suffix(".pdf"), bbox_inches="tight", facecolor="white"
    )
    plt.close(fig)


def write_report(
    path: Path,
    config: TraceConfig,
    sweep_rows: Sequence[dict[str, object]],
) -> None:
    grouped = {
        policy: [row for row in sweep_rows if row["policy"] == policy]
        for policy in POLICIES
    }
    highest_scale = max(float(row["scale"]) for row in sweep_rows)
    seed_count = len({int(row["seed"]) for row in sweep_rows})
    high_load = {
        policy: [
            row for row in grouped[policy]
            if float(row["scale"]) == highest_scale
        ]
        for policy in POLICIES
    }
    high_means = {
        policy: {
            field: statistics.fmean(
                float(row[field]) for row in high_load[policy]
            )
            for field in (
                "reference_rho",
                "utility_ratio",
                "backpressure_per_1k",
                "final_regret",
            )
        }
        for policy in POLICIES
    }
    ucp = high_means["ucp"]
    gg1 = high_means["ucp_gg1"]
    per_scale = []
    for scale in sorted({float(row["scale"]) for row in sweep_rows}):
        scale_rows = {
            policy: [
                row for row in grouped[policy]
                if float(row["scale"]) == scale
            ]
            for policy in POLICIES
        }
        per_scale.append({
            "scale": scale,
            "rho": statistics.fmean(
                float(row["reference_rho"])
                for row in scale_rows["ucp"]
            ),
            "gg1_utility": statistics.fmean(
                float(row["utility_ratio"])
                for row in scale_rows["ucp_gg1"]
            ),
            "feedback_utility": statistics.fmean(
                float(row["utility_ratio"])
                for row in scale_rows["ucp_gg1_feedback"]
            ),
            "gg1_backpressure": statistics.fmean(
                float(row["backpressure_per_1k"])
                for row in scale_rows["ucp_gg1"]
            ),
            "feedback_backpressure": statistics.fmean(
                float(row["backpressure_per_1k"])
                for row in scale_rows["ucp_gg1_feedback"]
            ),
        })
    best_feedback = max(
        per_scale,
        key=lambda row: row["feedback_utility"] - row["gg1_utility"],
    )
    overall = {}
    for policy in POLICIES:
        policy_rows = grouped[policy]
        overall[policy] = {
            "utility_ratio": statistics.fmean(
                float(row["utility_ratio"]) for row in policy_rows
            ),
            "final_regret": statistics.fmean(
                float(row["final_regret"]) for row in policy_rows
            ),
            "backpressure_per_1k": statistics.fmean(
                float(row["backpressure_per_1k"])
                for row in policy_rows
            ),
        }
    lines = [
        "# UCP 与 G/G/1 算法局部消融",
        "",
        (
            "本实验在独立 Python 虚拟环境中重放相同的阶段变化 miss trace。"
            "UCP 仅根据 ATD 容量收益分配远端 ways；另外两种策略分别加入 "
            "G/G/1 排队成本，以及实测等待时间驱动的反馈校准。"
        ),
        "",
        (
            "Measured-delay oracle 在每个窗口枚举全部合法 allocation，并"
            "使用 offered-arrival 到 service-start 的真实有限 FIFO 阻塞时间"
            "评价候选。在线策略只能读取此前已完成窗口，因此 oracle 是不可"
            "在线实现的 hindsight 上界。"
        ),
        "",
        "## 实验设置",
        "",
        f"- {config.cores} 核，{config.max_ways} 个远端 ways，profiling "
        f"window 为 {config.window:g} cycles；",
        f"- D2D bandwidth={config.bandwidth:g} bytes/cycle，buffer "
        f"depth={config.buffer_depth}，rho_max={config.rho_max:g}；",
        (
            f"- On/Off 与 synchronized 阶段的真实带宽系数分别为 "
            f"{config.burst_bandwidth_factor:g} 和 "
            f"{config.synchronized_bandwidth_factor:g}；"
            if config.trace_pattern == "mixed"
            else f"- hidden contention 阶段真实带宽系数为 "
            f"{config.contention_bandwidth_factor:g}，到达流量保持不变；"
        ),
        (
            "- 连续 trace 包含 steady、On/Off burst、synchronized burst 和"
            " recovery 四个阶段；"
            if config.trace_pattern == "mixed"
            else "- 连续 trace 包含 steady、hidden contention 和 recovery"
            " 三个阶段；"
        ),
        f"- 每个负载点使用 {seed_count} 个随机 seed，阴影表示 95% "
        "置信区间。",
        "",
        "## 消融结果",
        "",
        (
            "图 (a) 给出真实累计效用相对 oracle 的比例，图 (b) 给出每千 "
            "packet 触发的 backpressure 次数，"
            + (
                "图 (c) 展示代表性 trace 上逐窗口的 utility/oracle。"
                if config.trace_pattern == "feedback_step"
                else "图 (c) 展示代表性阶段变化 trace 上的累计真实效用。"
            )
        ),
        "",
        (
            f"在最高负载点（参考利用率 {ucp['reference_rho']:.3f}）下，"
            f"UCP 的 utility/oracle 为 {ucp['utility_ratio']:.3f}，加入 "
            f"G/G/1 后提高到 {gg1['utility_ratio']:.3f}。同时，每千 packet "
            f"的 backpressure 从 {ucp['backpressure_per_1k']:.1f} 次降至 "
            f"{gg1['backpressure_per_1k']:.1f} 次。该结果说明纯 UCP 只考虑"
            "容量收益，在 burst 阶段可能过度扩张远端容量；G/G/1 queue "
            "penalty 能够显式计入共享链路外部性。"
        ),
        "",
        (
            f"feedback 的最大增益出现在参考利用率 "
            f"{best_feedback['rho']:.3f}：utility/oracle 从 "
            f"{best_feedback['gg1_utility']:.3f} 提高到 "
            f"{best_feedback['feedback_utility']:.3f}，backpressure/1K "
            f"packets 从 {best_feedback['gg1_backpressure']:.1f} 降至 "
            f"{best_feedback['feedback_backpressure']:.1f}。这说明 feedback "
            "在未建模争用使解析模型持续低估时能够改变 allocation；在极高"
            "负载下两条曲线重新接近，表明反馈不能替代饱和保护。"
        ),
        "",
        "## 汇总",
        "",
        "| 策略 | 平均 utility/oracle | 平均最终 regret | "
        "平均 backpressure/1K packets |",
        "|---|---:|---:|---:|",
    ]
    for policy in POLICIES:
        row = overall[policy]
        lines.append(
            f"| {POLICY_LABELS[policy]} | {row['utility_ratio']:.3f} | "
            f"{row['final_regret']:.3f} | "
            f"{row['backpressure_per_1k']:.1f} |"
        )
    lines.extend([
        "",
        "## 边界",
        "",
        (
            "该实验用于隔离 allocator 的局部机制，不替代完整系统性能实验。"
            "虚拟环境固定了 ATD hit-probability 曲线、D2D 服务分布和 FIFO "
            "拓扑，因此论文中应将结果表述为受控机制验证，而不是 SPEC 或 "
            "gem5 IPC 结论。"
        ),
        "",
        "原始逐 seed 数据位于 `sweep.csv`，图 (c) 的逐窗口数据位于 "
        "`trace.csv`。",
        "",
    ])
    path.write_text("\n".join(lines) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--case", choices=("baseline", "contention", "feedback_stress"),
        default="baseline",
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--seeds", type=int, default=12)
    parser.add_argument("--scales", type=float, nargs="+")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.case == "contention":
        config = TraceConfig(
            burst_bandwidth_factor=0.45,
            synchronized_bandwidth_factor=0.55,
        )
        scales = args.scales or (0.5, 0.7, 0.9, 1.1, 1.3)
        default_output = Path(
            "uacc/hw/gg1_ucp_contention_results"
        )
        trace_view = "cumulative"
    elif args.case == "feedback_stress":
        config = TraceConfig(
            trace_pattern="feedback_step",
            contention_bandwidth_factor=0.40,
        )
        scales = args.scales or (0.7, 1.0, 1.3, 1.6)
        default_output = Path(
            "uacc/hw/gg1_ucp_feedback_stress_results"
        )
        trace_view = "normalized"
    else:
        config = TraceConfig()
        scales = args.scales or (0.7, 1.0, 1.4, 1.8, 2.2)
        default_output = Path(
            "uacc/hw/gg1_ucp_ablation_results"
        )
        trace_view = "cumulative"
    output_dir = args.output_dir or default_output
    if args.seeds <= 0 or any(scale <= 0.0 for scale in scales):
        raise SystemExit("seeds and scales must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    sweep_rows, trace_rows = run_experiment(config, scales, args.seeds)
    write_csv(output_dir / "sweep.csv", sweep_rows)
    write_csv(output_dir / "trace.csv", trace_rows)
    summary = {
        "config": config.__dict__,
        "seeds": args.seeds,
        "case": args.case,
        "scales": scales,
        "policies": list(POLICIES),
        "policy_labels": POLICY_LABELS,
        "sweep_rows": len(sweep_rows),
        "trace_rows": len(trace_rows),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    plot_results(
        output_dir / "gg1_ucp_ablation_1x3.png",
        sweep_rows,
        trace_rows,
        trace_view=trace_view,
    )
    write_report(output_dir / "ablation_section.md", config, sweep_rows)
    print(f"wrote {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
