#!/usr/bin/env python3
"""Standalone virtual traffic environment for the UACC G/G/1 model.

The simulator intentionally lives outside gem5.  It generates packet arrival
events, runs them through a FIFO single-server queue, and compares measured
queueing delay with M/G/1, G/G/1, and feedback-calibrated predictions.  The
online allocation path uses only the current-window traffic profile; candidate
allocations change predicted class rates and service mix, not future arrival
moments.  Future traces are used only by the measured-delay oracle.

Only the Python standard library is required.  Time is expressed in abstract
cycles; packet service time is ``size_bytes / bandwidth_bytes_per_cycle``.
"""

from __future__ import annotations

import argparse
from collections import deque
import itertools
import math
import random
import statistics
from dataclasses import dataclass
from typing import Iterable, Iterator, Optional, Sequence


EPSILON = 1.0e-12


@dataclass(frozen=True)
class Packet:
    offered_arrival: float
    size_bytes: int
    sequence: int = 0


@dataclass(frozen=True)
class Observation:
    packet: Packet
    arrival: float
    service_start: float
    departure: float
    service_time: float
    queue_wait: float
    in_system: int
    backpressured: bool


@dataclass
class QueueResult:
    observations: list[Observation]
    buffer_full_events: int
    backpressure_events: int
    max_occupancy: int

    @property
    def mean_wait(self) -> float:
        if not self.observations:
            return 0.0
        return statistics.fmean(item.queue_wait for item in self.observations)


@dataclass(frozen=True)
class MomentStats:
    count: int
    duration: float
    arrival_rate: float
    interarrival_mean: float
    ca2: float
    service_mean: float
    service_second_moment: float
    cs2: float
    utilization: float

    @classmethod
    def from_packets(
        cls,
        packets: Sequence[Packet],
        bandwidth_bytes_per_cycle: float,
        duration: float,
    ) -> "MomentStats":
        arrivals = [packet.offered_arrival for packet in packets]
        services = [
            packet.size_bytes / bandwidth_bytes_per_cycle for packet in packets
        ]
        return cls.from_samples(arrivals, services, duration)

    @classmethod
    def from_observations(
        cls,
        observations: Sequence[Observation],
        duration: float,
    ) -> "MomentStats":
        arrivals = [item.arrival for item in observations]
        services = [item.service_time for item in observations]
        return cls.from_samples(arrivals, services, duration)

    @classmethod
    def from_samples(
        cls,
        arrivals: Sequence[float],
        services: Sequence[float],
        duration: float,
    ) -> "MomentStats":
        if len(arrivals) != len(services):
            raise ValueError("arrivals and services must have equal lengths")
        if duration <= 0.0:
            raise ValueError("duration must be positive")

        count = len(arrivals)
        rate = count / duration
        if count < 2:
            interarrival_mean = 0.0
            ca2 = 1.0
        else:
            interarrivals = [
                right - left for left, right in zip(arrivals, arrivals[1:])
            ]
            interarrival_mean = statistics.fmean(interarrivals)
            ca2 = _population_variance(interarrivals) / max(
                interarrival_mean * interarrival_mean, EPSILON
            )

        if count == 0:
            service_mean = 0.0
            service_second_moment = 0.0
            cs2 = 0.0
        else:
            service_mean = statistics.fmean(services)
            service_second_moment = statistics.fmean(
                service * service for service in services
            )
            cs2 = max(
                0.0,
                (service_second_moment - service_mean * service_mean)
                / max(service_mean * service_mean, EPSILON),
            )

        return cls(
            count=count,
            duration=duration,
            arrival_rate=rate,
            interarrival_mean=interarrival_mean,
            ca2=max(0.0, ca2),
            service_mean=service_mean,
            service_second_moment=service_second_moment,
            cs2=cs2,
            utilization=rate * service_mean,
        )


def _population_variance(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    count = 0
    mean = 0.0
    m2 = 0.0
    for value in values:
        count += 1
        delta = value - mean
        mean += delta / count
        m2 += delta * (value - mean)
    return m2 / count


def kingman_wait(
    stats: MomentStats,
    *,
    use_effective_ca2: bool = True,
    saturation_limit: float = 0.999999,
) -> float:
    """Return the Kingman G/G/1 mean queue wait in cycles."""

    if stats.count == 0 or stats.arrival_rate <= 0.0:
        return 0.0
    if stats.service_mean <= 0.0 or stats.utilization >= saturation_limit:
        return math.inf
    ca2 = max(1.0, stats.ca2) if use_effective_ca2 else stats.ca2
    return (
        stats.utilization
        / (1.0 - stats.utilization)
        * (ca2 + stats.cs2)
        / 2.0
        * stats.service_mean
    )


def mg1_wait(
    stats: MomentStats, *, saturation_limit: float = 0.999999
) -> float:
    """Return the M/G/1 special case of the same moment formula."""

    return _kingman_with_ca2(stats, 1.0, saturation_limit)


def _kingman_with_ca2(
    stats: MomentStats, ca2: float, saturation_limit: float
) -> float:
    if stats.count == 0 or stats.arrival_rate <= 0.0:
        return 0.0
    if stats.service_mean <= 0.0 or stats.utilization >= saturation_limit:
        return math.inf
    return (
        stats.utilization
        / (1.0 - stats.utilization)
        * (ca2 + stats.cs2)
        / 2.0
        * stats.service_mean
    )


class FeedbackCalibrator:
    """EWMA calibration factor matching the model.md definition."""

    def __init__(
        self,
        *,
        alpha: float = 0.2,
        beta_max: float = 4.0,
        initial_beta: float = 1.0,
    ) -> None:
        if not 0.0 < alpha <= 1.0:
            raise ValueError("alpha must be in (0, 1]")
        if beta_max < 1.0:
            raise ValueError("beta_max must be at least one")
        self.alpha = alpha
        self.beta_max = beta_max
        self.beta = min(max(initial_beta, 1.0), beta_max)

    def update(self, observed_wait: float, model_wait: float) -> float:
        if not math.isfinite(model_wait) or model_wait <= EPSILON:
            raw_beta = self.beta_max if observed_wait > EPSILON else 1.0
        else:
            raw_beta = observed_wait / max(model_wait, EPSILON)
            raw_beta = min(max(raw_beta, 1.0), self.beta_max)
        self.beta = self.alpha * raw_beta + (1.0 - self.alpha) * self.beta
        self.beta = min(max(self.beta, 1.0), self.beta_max)
        return self.beta

    def predict(self, stats: MomentStats) -> float:
        model_wait = kingman_wait(stats)
        if not math.isfinite(model_wait):
            return model_wait
        return self.beta * model_wait


def poisson_arrivals(
    rate: float, duration: float, rng: random.Random, *, start: float = 0.0
) -> list[float]:
    if rate <= 0.0 or duration <= start:
        return []
    result = []
    time = start
    while True:
        time += rng.expovariate(rate)
        if time >= duration:
            break
        result.append(time)
    return result


def deterministic_arrivals(
    rate: float, duration: float, *, start: float = 0.0
) -> list[float]:
    if rate <= 0.0 or duration <= start:
        return []
    period = 1.0 / rate
    count = int(math.floor((duration - start) / period))
    return [start + index * period for index in range(1, count + 1)]


def on_off_arrivals(
    rate_on: float,
    on_duration: float,
    off_duration: float,
    duration: float,
    rng: random.Random,
) -> list[float]:
    if rate_on <= 0.0 or on_duration <= 0.0 or off_duration < 0.0:
        return []
    result = []
    phase = 0.0
    while phase < duration:
        result.extend(
            poisson_arrivals(
                rate_on,
                min(phase + on_duration, duration),
                rng,
                start=phase,
            )
        )
        phase += on_duration + off_duration
    return result


def synchronized_bursts(
    period: float,
    burst_size: int,
    duration: float,
    *,
    jitter: float = 0.0,
    rng: Optional[random.Random] = None,
) -> list[float]:
    if period <= 0.0 or burst_size < 1 or duration <= 0.0:
        return []
    rng = rng or random.Random(0)
    result = []
    burst_time = 0.0
    while burst_time < duration:
        for _ in range(burst_size):
            arrival = burst_time
            if jitter:
                arrival += rng.uniform(-jitter, jitter)
            if 0.0 <= arrival < duration:
                result.append(arrival)
        burst_time += period
    return sorted(result)


def make_trace(
    arrivals: Iterable[float],
    rng: random.Random,
    *,
    sizes: Sequence[int] = (8, 64),
    probabilities: Sequence[float] = (0.35, 0.65),
) -> list[Packet]:
    if len(sizes) != len(probabilities) or not sizes:
        raise ValueError(
            "sizes and probabilities must have equal non-zero length"
        )
    if any(size <= 0 for size in sizes):
        raise ValueError("packet sizes must be positive")
    if abs(sum(probabilities) - 1.0) > 1.0e-9:
        raise ValueError("packet probabilities must sum to one")

    result = []
    for sequence, arrival in enumerate(arrivals):
        result.append(
            Packet(
                offered_arrival=float(arrival),
                size_bytes=rng.choices(sizes, weights=probabilities, k=1)[0],
                sequence=sequence,
            )
        )
    return result


def simulate_fifo(
    packets: Sequence[Packet],
    bandwidth_bytes_per_cycle: float,
    *,
    buffer_depth: Optional[int] = None,
) -> QueueResult:
    """Simulate a work-conserving FIFO queue.

    ``buffer_depth`` counts waiting slots in addition to the packet currently
    in service.  A full finite buffer applies backpressure by delaying the
    packet's actual enqueue time until a slot is released.  No packet is
    dropped, so the experiment measures congestion rather than loss.
    """

    if bandwidth_bytes_per_cycle <= 0.0:
        raise ValueError("bandwidth must be positive")
    if buffer_depth is not None and buffer_depth < 0:
        raise ValueError("buffer_depth must be non-negative")

    server_free = 0.0
    departures = deque()
    observations = []
    buffer_full_events = 0
    max_occupancy = 0

    for packet in packets:
        arrival = packet.offered_arrival
        backpressured = False

        while departures and departures[0] <= arrival:
            departures.popleft()

        if buffer_depth is not None:
            capacity = buffer_depth + 1
            while len(departures) >= capacity:
                backpressured = True
                arrival = max(arrival, departures[0])
                while departures and departures[0] <= arrival:
                    departures.popleft()
            if backpressured:
                buffer_full_events += 1

        service_time = packet.size_bytes / bandwidth_bytes_per_cycle
        service_start = max(arrival, server_free)
        departure = service_start + service_time
        departures.append(departure)
        server_free = departure
        in_system = len(departures)
        max_occupancy = max(max_occupancy, in_system)
        observations.append(
            Observation(
                packet=packet,
                arrival=arrival,
                service_start=service_start,
                departure=departure,
                service_time=service_time,
                queue_wait=service_start - arrival,
                in_system=in_system,
                backpressured=backpressured,
            )
        )

    return QueueResult(
        observations=observations,
        buffer_full_events=buffer_full_events,
        backpressure_events=buffer_full_events,
        max_occupancy=max_occupancy,
    )


@dataclass(frozen=True)
class WindowMetric:
    window_start: float
    window_end: float
    offered_count: int
    observed_count: int
    ca2: float
    cs2: float
    utilization: float
    observed_wait: float
    mg1_wait: float
    gg1_wait: float
    feedback_wait: float
    beta_before: float


def window_metrics(
    packets: Sequence[Packet],
    queue: QueueResult,
    duration: float,
    window: float,
    bandwidth_bytes_per_cycle: float,
    *,
    calibrator: Optional[FeedbackCalibrator] = None,
    rho_max: float = 0.90,
) -> list[WindowMetric]:
    if duration <= 0.0 or window <= 0.0:
        raise ValueError("duration and window must be positive")
    if not packets:
        return []

    count = int(math.ceil(duration / window))
    observed_by_sequence = {
        item.packet.sequence: item for item in queue.observations
    }
    result = []
    for index in range(count):
        start = index * window
        end = min(duration, start + window)
        offered = [
            packet
            for packet in packets
            if start <= packet.offered_arrival < end
        ]
        observed = [
            observed_by_sequence[packet.sequence]
            for packet in offered
            if packet.sequence in observed_by_sequence
        ]
        if not offered:
            continue

        offered_stats = MomentStats.from_packets(
            offered, bandwidth_bytes_per_cycle, max(end - start, EPSILON)
        )
        observed_wait = (
            statistics.fmean(item.queue_wait for item in observed)
            if observed
            else 0.0
        )
        beta_before = calibrator.beta if calibrator else 1.0
        mg1 = mg1_wait(offered_stats)
        gg1 = kingman_wait(offered_stats)
        feedback = beta_before * gg1
        result.append(
            WindowMetric(
                window_start=start,
                window_end=end,
                offered_count=len(offered),
                observed_count=len(observed),
                ca2=offered_stats.ca2,
                cs2=offered_stats.cs2,
                utilization=offered_stats.utilization,
                observed_wait=observed_wait,
                mg1_wait=mg1,
                gg1_wait=gg1,
                feedback_wait=feedback,
                beta_before=beta_before,
            )
        )
        if calibrator and observed:
            calibrator.update(observed_wait, gg1)
    return result


def mean_absolute_relative_error(
    predictions: Iterable[float], observations: Iterable[float]
) -> float:
    errors = []
    for prediction, observation in zip(predictions, observations):
        if observation > EPSILON and math.isfinite(prediction):
            errors.append(abs(prediction - observation) / observation)
    return statistics.fmean(errors) if errors else 0.0


def mean_underestimation(
    predictions: Iterable[float], observations: Iterable[float]
) -> float:
    errors = []
    for prediction, observation in zip(predictions, observations):
        if observation > EPSILON and math.isfinite(prediction):
            errors.append(max(0.0, observation - prediction) / observation)
    return statistics.fmean(errors) if errors else 0.0


@dataclass(frozen=True)
class AllocationScore:
    allocation: tuple[int, ...]
    capacity_gain: float
    fixed_cost: float
    queue_cost: float
    objective: float
    valid: bool
    utilization: float
    observed_wait: float
    predicted_wait: float


@dataclass(frozen=True)
class CurrentTrafficProfile:
    """Moments measured under the allocation currently in deployment."""

    stats: MomentStats
    core_miss_rates: tuple[float, ...]


@dataclass(frozen=True)
class CandidateRateSums:
    """Rate-weighted service sums used by the direct queue-cost formula."""

    l0: float
    l1: float
    l2: float


@dataclass(frozen=True)
class CandidateWindowSums:
    """Per-window packet, busy-cycle, and squared-service sums."""

    r0: float
    r1: float
    r2: float


def measure_current_profile(
    core_misses: Sequence[Sequence[float]],
    current_allocation: Sequence[int],
    hit_probabilities: Sequence[Sequence[float]],
    *,
    duration: float,
    bandwidth_bytes_per_cycle: float,
) -> CurrentTrafficProfile:
    """Measure current-window moments without inspecting a candidate trace."""

    packets = build_allocation_trace(
        core_misses, current_allocation, hit_probabilities
    )
    stats = MomentStats.from_packets(
        packets, bandwidth_bytes_per_cycle, duration
    )
    miss_rates = tuple(len(misses) / duration for misses in core_misses)
    return CurrentTrafficProfile(stats=stats, core_miss_rates=miss_rates)


def candidate_packet_rates(
    profile: CurrentTrafficProfile,
    allocation: Sequence[int],
    hit_probabilities: Sequence[Sequence[float]],
    *,
    bandwidth_bytes_per_cycle: float,
    request_size: int = 8,
    hit_response_size: int = 64,
    miss_response_size: int = 8,
) -> tuple[tuple[float, float], ...]:
    """Predict class rates and service times from current rates and ATD mix."""

    if len(profile.core_miss_rates) != len(allocation):
        raise ValueError("core input lengths do not match")
    rates = []
    for core_id, (miss_rate, ways, probabilities) in enumerate(
        zip(profile.core_miss_rates, allocation, hit_probabilities)
    ):
        if ways < 0 or ways >= len(probabilities):
            raise ValueError("allocation exceeds hit-probability table")
        if ways == 0:
            continue
        hit_probability = probabilities[ways]
        request_service = request_size / bandwidth_bytes_per_cycle
        hit_service = hit_response_size / bandwidth_bytes_per_cycle
        miss_service = miss_response_size / bandwidth_bytes_per_cycle
        rates.append((miss_rate, request_service))
        rates.append((miss_rate * hit_probability, hit_service))
        rates.append((miss_rate * (1.0 - hit_probability), miss_service))
    return tuple(rates)


def candidate_rate_sums(
    rates: Sequence[tuple[float, float]],
) -> CandidateRateSums:
    """Return L0=sum(r), L1=sum(r*S), and L2=sum(r*S^2)."""

    return CandidateRateSums(
        l0=math.fsum(rate for rate, _ in rates),
        l1=math.fsum(rate * service for rate, service in rates),
        l2=math.fsum(
            rate * service * service for rate, service in rates
        ),
    )


def direct_queue_cost(
    sums: CandidateRateSums,
    ca2: float,
    *,
    beta: float = 1.0,
    saturation_limit: float = 0.999999,
) -> float:
    """Return lambda*Wq using the simplified Section 14 expression."""

    if sums.l0 <= 0.0 or sums.l1 <= 0.0:
        return 0.0
    if sums.l1 >= saturation_limit:
        return math.inf
    effective_ca2 = max(1.0, ca2)
    numerator = (
        sums.l1 * sums.l1 * (effective_ca2 - 1.0)
        + sums.l0 * sums.l2
    )
    return beta * numerator / (2.0 * (1.0 - sums.l1))


def window_sums_from_rates(
    sums: CandidateRateSums, duration: float
) -> CandidateWindowSums:
    if duration <= 0.0:
        raise ValueError("duration must be positive")
    return CandidateWindowSums(
        r0=sums.l0 * duration,
        r1=sums.l1 * duration,
        r2=sums.l2 * duration,
    )


def window_queue_cost(
    sums: CandidateWindowSums,
    duration: float,
    ca2: float,
    *,
    beta: float = 1.0,
    saturation_limit: float = 0.999999,
) -> float:
    """Return queueing stall cycles per window using count-domain sums."""

    if duration <= 0.0:
        raise ValueError("duration must be positive")
    if sums.r0 <= 0.0 or sums.r1 <= 0.0:
        return 0.0
    if sums.r1 >= saturation_limit * duration:
        return math.inf
    effective_ca2 = max(1.0, ca2)
    numerator = (
        sums.r1 * sums.r1 * (effective_ca2 - 1.0)
        + sums.r0 * sums.r2
    )
    return beta * numerator / (2.0 * (duration - sums.r1))


def moments_from_candidate_rates(
    profile: CurrentTrafficProfile,
    allocation: Sequence[int],
    hit_probabilities: Sequence[Sequence[float]],
    *,
    duration: float,
    bandwidth_bytes_per_cycle: float,
) -> MomentStats:
    """Build candidate service moments while reusing current-window CA2."""

    rates = candidate_packet_rates(
        profile,
        allocation,
        hit_probabilities,
        bandwidth_bytes_per_cycle=bandwidth_bytes_per_cycle,
    )
    sums = candidate_rate_sums(rates)
    arrival_rate = sums.l0
    if arrival_rate <= 0.0:
        return MomentStats.from_samples([], [], duration)

    service_mean = sums.l1 / arrival_rate
    service_second_moment = sums.l2 / arrival_rate
    cs2 = max(
        0.0,
        (service_second_moment - service_mean * service_mean)
        / max(service_mean * service_mean, EPSILON),
    )
    count = max(1, int(round(arrival_rate * duration)))
    return MomentStats(
        count=count,
        duration=duration,
        arrival_rate=arrival_rate,
        interarrival_mean=1.0 / arrival_rate,
        ca2=profile.stats.ca2,
        service_mean=service_mean,
        service_second_moment=service_second_moment,
        cs2=cs2,
        utilization=sums.l1,
    )


def allocation_vectors(
    num_cores: int, max_ways: int
) -> Iterator[tuple[int, ...]]:
    for vector in itertools.product(range(max_ways + 1), repeat=num_cores):
        if sum(vector) <= max_ways:
            yield vector


def build_allocation_trace(
    core_misses: Sequence[Sequence[float]],
    allocation: Sequence[int],
    hit_probabilities: Sequence[Sequence[float]],
    *,
    request_size: int = 8,
    hit_response_size: int = 64,
    miss_response_size: int = 8,
    response_delay: float = 20.0,
) -> list[Packet]:
    if len(core_misses) != len(allocation) or len(allocation) != len(
        hit_probabilities
    ):
        raise ValueError("core input lengths do not match")

    packets = []
    sequence = 0
    for core_id, (misses, ways, probabilities) in enumerate(
        zip(core_misses, allocation, hit_probabilities)
    ):
        if ways < 0 or ways >= len(probabilities):
            raise ValueError("allocation exceeds hit-probability table")
        if ways == 0:
            continue
        hit_probability = probabilities[ways]
        for miss_index, miss_time in enumerate(misses):
            # A deterministic pseudo-random value makes all candidate
            # allocations compare the same miss stream and hit decisions.
            sample = ((miss_index + 1) * 0x9E3779B1 + (core_id + 1)) % 1000003
            hit = sample / 1000003.0 < hit_probability
            packets.append(Packet(miss_time, request_size, sequence))
            sequence += 1
            packets.append(
                Packet(
                    miss_time + response_delay,
                    hit_response_size if hit else miss_response_size,
                    sequence,
                )
            )
            sequence += 1
    return sorted(
        packets, key=lambda packet: (packet.offered_arrival, packet.sequence)
    )


def score_allocation(
    core_misses: Sequence[Sequence[float]],
    allocation: Sequence[int],
    hit_probabilities: Sequence[Sequence[float]],
    *,
    duration: float,
    bandwidth_bytes_per_cycle: float,
    lower_miss_penalty_cycles: float,
    fixed_latency_cycles: float,
    base_beta: float = 1.0,
    rho_max: float = 0.90,
    use_feedback: bool = False,
    model: str = "gg1",
    buffer_depth: Optional[int] = None,
    current_profile: Optional[CurrentTrafficProfile] = None,
    reference_allocation: Optional[Sequence[int]] = None,
) -> AllocationScore:
    if model not in ("mg1", "gg1", "direct", "window_direct"):
        raise ValueError(
            "model must be 'mg1', 'gg1', 'direct', or 'window_direct'"
        )
    if current_profile is None:
        if reference_allocation is None:
            reference_allocation = allocation
        current_profile = measure_current_profile(
            core_misses,
            reference_allocation,
            hit_probabilities,
            duration=duration,
            bandwidth_bytes_per_cycle=bandwidth_bytes_per_cycle,
        )

    rates = candidate_packet_rates(
        current_profile,
        allocation,
        hit_probabilities,
        bandwidth_bytes_per_cycle=bandwidth_bytes_per_cycle,
    )
    sums = candidate_rate_sums(rates)
    if sums.l0 <= 0.0:
        return AllocationScore(
            tuple(allocation),
            capacity_gain=0.0,
            fixed_cost=0.0,
            queue_cost=0.0,
            objective=0.0,
            valid=True,
            utilization=0.0,
            observed_wait=0.0,
            predicted_wait=0.0,
        )

    beta = base_beta if use_feedback else 1.0
    if model == "window_direct":
        window_sums = window_sums_from_rates(sums, duration)
        queue_cost_window = window_queue_cost(
            window_sums,
            duration,
            current_profile.stats.ca2,
            beta=beta,
        )
        queue_cost = queue_cost_window / duration
        predicted_wait = queue_cost_window / window_sums.r0
        utilization = window_sums.r1 / duration
    elif model == "direct":
        queue_cost = direct_queue_cost(
            sums,
            current_profile.stats.ca2,
            beta=beta,
        )
        predicted_wait = queue_cost / sums.l0
        utilization = sums.l1
    else:
        stats = moments_from_candidate_rates(
            current_profile,
            allocation,
            hit_probabilities,
            duration=duration,
            bandwidth_bytes_per_cycle=bandwidth_bytes_per_cycle,
        )
        predicted_wait = (
            kingman_wait(stats)
            if model == "gg1"
            else mg1_wait(stats)
        )
        predicted_wait *= beta
        queue_cost = stats.arrival_rate * predicted_wait
        utilization = stats.utilization
    valid = utilization < rho_max

    capacity_gain = 0.0
    for core_id, miss_rate in enumerate(current_profile.core_miss_rates):
        ways = allocation[core_id]
        p = hit_probabilities[core_id][ways]
        capacity_gain += miss_rate * p * lower_miss_penalty_cycles
    packet_rate = sums.l0
    fixed_cost = packet_rate * fixed_latency_cycles
    objective = capacity_gain - fixed_cost - queue_cost
    if not valid:
        objective = -math.inf
    return AllocationScore(
        tuple(allocation),
        capacity_gain,
        fixed_cost,
        queue_cost,
        objective,
        valid,
        utilization,
        0.0,
        predicted_wait,
    )


def oracle_score_allocation(
    core_misses: Sequence[Sequence[float]],
    allocation: Sequence[int],
    hit_probabilities: Sequence[Sequence[float]],
    *,
    duration: float,
    bandwidth_bytes_per_cycle: float,
    lower_miss_penalty_cycles: float,
    fixed_latency_cycles: float,
    rho_max: float = 0.90,
    buffer_depth: Optional[int] = None,
) -> AllocationScore:
    # This function is deliberately an offline measured-delay oracle.  It is
    # never used to provide candidate moments to score_allocation().
    packets = build_allocation_trace(
        core_misses, allocation, hit_probabilities
    )
    if not packets:
        return AllocationScore(
            tuple(allocation), 0.0, 0.0, 0.0, 0.0, True, 0.0, 0.0, 0.0
        )
    stats = MomentStats.from_packets(
        packets, bandwidth_bytes_per_cycle, duration
    )
    queue = simulate_fifo(
        packets,
        bandwidth_bytes_per_cycle,
        buffer_depth=buffer_depth,
    )
    capacity_gain = 0.0
    for core_id, misses in enumerate(core_misses):
        ways = allocation[core_id]
        p = hit_probabilities[core_id][ways]
        capacity_gain += (
            len(misses) / duration * p * lower_miss_penalty_cycles
        )
    packet_rate = len(packets) / duration
    fixed_cost = packet_rate * fixed_latency_cycles
    queue_cost = packet_rate * queue.mean_wait
    objective = capacity_gain - fixed_cost - queue_cost
    valid = stats.utilization < rho_max
    return AllocationScore(
        tuple(allocation),
        capacity_gain,
        fixed_cost,
        queue_cost,
        objective if valid else -math.inf,
        valid,
        stats.utilization,
        queue.mean_wait,
        kingman_wait(stats),
    )


def _scenario_trace(name: str, duration: float, seed: int) -> list[Packet]:
    rng = random.Random(seed)
    if name == "poisson":
        arrivals = poisson_arrivals(0.12, duration, rng)
    elif name == "deterministic":
        arrivals = deterministic_arrivals(0.12, duration)
    elif name == "onoff":
        arrivals = on_off_arrivals(0.22, 120.0, 180.0, duration, rng)
    elif name == "synchronized":
        arrivals = synchronized_bursts(100.0, 12, duration)
    else:
        raise ValueError(f"unknown scenario: {name}")
    return make_trace(arrivals, rng)


def run_scenario_report(
    *, duration: float = 100_000.0, bandwidth: float = 8.0, seed: int = 7
) -> list[dict[str, float | int | str]]:
    rows = []
    for name in ("poisson", "deterministic", "onoff", "synchronized"):
        packets = _scenario_trace(name, duration, seed)
        queue = simulate_fifo(packets, bandwidth, buffer_depth=8)
        calibrator = FeedbackCalibrator()
        metrics = window_metrics(
            packets,
            queue,
            duration,
            5_000.0,
            bandwidth,
            calibrator=calibrator,
        )
        observed = [item.observed_wait for item in metrics]
        mg1 = [item.mg1_wait for item in metrics]
        gg1 = [item.gg1_wait for item in metrics]
        feedback = [item.feedback_wait for item in metrics]
        rows.append(
            {
                "scenario": name,
                "packets": len(packets),
                "ca2": statistics.fmean(item.ca2 for item in metrics),
                "rho": statistics.fmean(item.utilization for item in metrics),
                "observed_wait": queue.mean_wait,
                "mg1_mare": mean_absolute_relative_error(mg1, observed),
                "gg1_mare": mean_absolute_relative_error(gg1, observed),
                "feedback_mare": mean_absolute_relative_error(
                    feedback, observed
                ),
                "mg1_under": mean_underestimation(mg1, observed),
                "gg1_under": mean_underestimation(gg1, observed),
                "feedback_under": mean_underestimation(feedback, observed),
                "backpressure": queue.backpressure_events,
            }
        )
    return rows


def run_allocation_report(
    *, duration: float = 100_000.0, seed: int = 19
) -> dict[str, object]:
    # Use one window for measurement and a separate window for evaluation.
    # The allocator only sees current_core_misses; next_core_misses is used
    # solely by the measured-delay oracle after the decision is made.
    window = duration
    rng = random.Random(seed)
    all_core_misses = [
        on_off_arrivals(0.14, 100.0, 160.0, 2.0 * window, rng),
        poisson_arrivals(0.045, 2.0 * window, rng),
    ]
    current_core_misses = [
        [time for time in misses if time < window]
        for misses in all_core_misses
    ]
    next_core_misses = [
        [time - window for time in misses if window <= time < 2.0 * window]
        for misses in all_core_misses
    ]
    hit_probabilities = ([0.0, 0.42, 0.70, 0.80], [0.0, 0.10, 0.23, 0.30])
    candidates = list(allocation_vectors(2, 3))
    common = dict(
        duration=window,
        bandwidth_bytes_per_cycle=3.0,
        lower_miss_penalty_cycles=300.0,
        fixed_latency_cycles=20.0,
        rho_max=0.90,
        buffer_depth=32,
    )
    current_allocation = (1, 1)
    current_profile = measure_current_profile(
        current_core_misses,
        current_allocation,
        hit_probabilities,
        duration=window,
        bandwidth_bytes_per_cycle=common["bandwidth_bytes_per_cycle"],
    )
    current_packets = build_allocation_trace(
        current_core_misses, current_allocation, hit_probabilities
    )
    current_queue = simulate_fifo(
        current_packets,
        common["bandwidth_bytes_per_cycle"],
        buffer_depth=common["buffer_depth"],
    )
    oracle = [
        oracle_score_allocation(
            next_core_misses, allocation, hit_probabilities, **common
        )
        for allocation in candidates
    ]
    predicted = [
        score_allocation(
            current_core_misses,
            allocation,
            hit_probabilities,
            **common,
            current_profile=current_profile,
        )
        for allocation in candidates
    ]

    # Update feedback only after the current window has completed.  The
    # resulting beta is then used to score the next-window candidates.
    calibrator = FeedbackCalibrator()
    calibrator.update(
        current_queue.mean_wait, kingman_wait(current_profile.stats)
    )
    beta = calibrator.beta
    feedback = [
        score_allocation(
            current_core_misses,
            allocation,
            hit_probabilities,
            **common,
            base_beta=beta,
            use_feedback=True,
            current_profile=current_profile,
        )
        for allocation in candidates
    ]
    direct = [
        score_allocation(
            current_core_misses,
            allocation,
            hit_probabilities,
            **common,
            model="direct",
            current_profile=current_profile,
        )
        for allocation in candidates
    ]
    direct_feedback = [
        score_allocation(
            current_core_misses,
            allocation,
            hit_probabilities,
            **common,
            model="direct",
            base_beta=beta,
            use_feedback=True,
            current_profile=current_profile,
        )
        for allocation in candidates
    ]
    window_direct = [
        score_allocation(
            current_core_misses,
            allocation,
            hit_probabilities,
            **common,
            model="window_direct",
            current_profile=current_profile,
        )
        for allocation in candidates
    ]
    window_direct_feedback = [
        score_allocation(
            current_core_misses,
            allocation,
            hit_probabilities,
            **common,
            model="window_direct",
            base_beta=beta,
            use_feedback=True,
            current_profile=current_profile,
        )
        for allocation in candidates
    ]

    def best(scores: Sequence[AllocationScore]) -> AllocationScore:
        return max(scores, key=lambda score: score.objective)

    return {
        "oracle": best(oracle),
        "mg1": best(
            [
                score_allocation(
                    current_core_misses,
                    score.allocation,
                    hit_probabilities,
                    **common,
                    model="mg1",
                    base_beta=1.0,
                    use_feedback=False,
                    current_profile=current_profile,
                )
                for score in predicted
            ]
        ),
        "gg1": best(predicted),
        "feedback": best(feedback),
        "direct": best(direct),
        "direct_feedback": best(direct_feedback),
        "window_direct": best(window_direct),
        "window_direct_feedback": best(window_direct_feedback),
        "beta": beta,
        "candidate_count": len(candidates),
    }


def _format_scenario_report(rows: Sequence[dict[str, object]]) -> str:
    lines = [
        "scenario        packets  CA2   rho   Wobs    M/G/1 MARE  G/G/1 MARE  "
        "FB MARE  FB under  backpressure",
        "---------------- ------- ----- ----- ------- ----------- ----------- "
        "-------- -------- ----------",
    ]
    for row in rows:
        lines.append(
            f"{str(row['scenario']):16} {int(row['packets']):7d} "
            f"{float(row['ca2']):5.2f} {float(row['rho']):5.2f} "
            f"{float(row['observed_wait']):7.2f} "
            f"{float(row['mg1_mare']):11.3f} {float(row['gg1_mare']):11.3f} "
            f"{float(row['feedback_mare']):8.3f} "
            f"{float(row['feedback_under']):8.3f} "
            f"{int(row['backpressure']):10d}"
        )
    return "\n".join(lines)


def _format_allocation_report(report: dict[str, object]) -> str:
    lines = ["allocation ranking (higher objective is better)"]
    for key in (
        "oracle",
        "mg1",
        "gg1",
        "feedback",
        "direct",
        "direct_feedback",
        "window_direct",
        "window_direct_feedback",
    ):
        score = report[key]
        assert isinstance(score, AllocationScore)
        lines.append(
            f"  {key:8} allocation={score.allocation} "
            f"objective={score.objective:8.4f} "
            f"rho={score.utilization:5.2f} wait={score.observed_wait:8.2f} "
            f"pred_wait={score.predicted_wait:8.2f}"
        )
    lines.append(f"  feedback beta={float(report['beta']):.3f}")
    lines.append(f"  candidates={int(report['candidate_count'])}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the standalone UACC G/G/1 virtual traffic experiment."
    )
    parser.add_argument("--duration", type=float, default=100_000.0)
    parser.add_argument("--bandwidth", type=float, default=8.0)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    scenario_rows = run_scenario_report(
        duration=args.duration, bandwidth=args.bandwidth, seed=args.seed
    )
    print(_format_scenario_report(scenario_rows))
    print()
    print(
        _format_allocation_report(
            run_allocation_report(duration=args.duration)
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
