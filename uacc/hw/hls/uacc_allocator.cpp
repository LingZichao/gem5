#include "uacc_allocator.hpp"

using namespace uacc_hls;

// The native ap_uint division operator makes Vitis HLS 2019.2 build a fully
// spatially-unrolled divider with one quotient bit per pipeline stage.  That
// is useful for throughput-oriented datapaths, but the allocator runs only at
// a profiling-window boundary.  Use one rolled restoring divider for both
// CA2 and feedback-beta updates instead.  The result is still exact truncating
// integer division; only latency changes.
static SharedDividerQ
sharedWideDivide(
    SharedDividerQ numerator, SharedDividerDenominatorQ denominator)
{
#pragma HLS INLINE off
    SharedDividerQ quotient = 0;
    ap_uint<SharedDividerDenominatorBits + 1> remainder = 0;

    for (int bit = SharedDividerBits - 1; bit >= 0; --bit) {
#pragma HLS LOOP_TRIPCOUNT min=136 max=136
#pragma HLS UNROLL factor=1
#pragma HLS PIPELINE II=3
        remainder <<= 1;
        remainder[0] = numerator[bit];
        if (remainder >= denominator) {
            remainder -= denominator;
            quotient[bit] = 1;
        }
    }
    return quotient;
}

static FactorQ
shiftEwma(FactorQ previous, FactorQ sample, ap_uint<4> shift,
          FactorQ maximum)
{
    ap_int<FactorBits + 2> difference =
        ap_int<FactorBits + 2>(sample) -
        ap_int<FactorBits + 2>(previous);
    ap_int<FactorBits + 2> updated =
        ap_int<FactorBits + 2>(previous) + (difference >> shift);
    if (updated < 0)
        return 0;
    if (updated > ap_int<FactorBits + 2>(maximum))
        return maximum;
    return FactorQ(updated);
}

static FactorQ
arrivalCa2(ArrivalCount samples, ArrivalSum sum, ArrivalSquareSum square_sum,
           FactorQ previous, FactorQ maximum, ap_uint<4> ewma_shift)
{
    if (samples == 0)
        return previous;

    FactorQ raw = maximum;
    if (sum != 0) {
        const SharedDividerQ numerator =
            (SharedDividerQ(samples) * SharedDividerQ(square_sum))
            << FractionBits;
        const SharedDividerDenominatorQ denominator =
            SharedDividerDenominatorQ(sum) *
            SharedDividerDenominatorQ(sum);
        const SharedDividerQ ratio_q =
            sharedWideDivide(numerator, denominator);
        const FactorQ one_q = FactorQ(1) << FractionBits;
        if (ratio_q <= one_q) {
            raw = 0;
        } else if (ratio_q - one_q >= maximum) {
            raw = maximum;
        } else {
            raw = FactorQ(ratio_q - one_q);
        }
    }
    return shiftEwma(previous, raw, ewma_shift, maximum);
}

static FactorQ
feedbackBeta(WaitSum observed_wait, CostQ current_model_q,
             FactorQ previous, FactorQ maximum, ap_uint<4> ewma_shift)
{
    const FactorQ one_q = FactorQ(1) << FractionBits;
    ap_uint<CostBits> denominator = current_model_q;
    if (denominator == 0)
        denominator = 1;
    const SharedDividerQ numerator =
        SharedDividerQ(observed_wait) << (2 * FractionBits);
    const SharedDividerQ ratio_q = sharedWideDivide(
        numerator, SharedDividerDenominatorQ(denominator));
    FactorQ raw = one_q;
    if (ratio_q >= maximum)
        raw = maximum;
    else if (ratio_q > one_q)
        raw = FactorQ(ratio_q);
    return shiftEwma(previous, raw, ewma_shift, maximum);
}

static void
trafficSums(const Count counts[PacketClasses],
            const ServiceCycles service[PacketClasses],
            const ServiceSquare square[PacketClasses],
            Count &r0, BusySum &r1, ServiceSecondSum &r2,
            ap_uint<1> &saturated)
{
    ap_uint<CountBits + 1> count_sum = 0;
    ap_uint<BusySumBits + 1> busy_sum = 0;
    ap_uint<ServiceSecondSumBits + 1> second_sum = 0;
    unsigned packet = 0;
    while (packet < PacketClasses) {
#pragma HLS LOOP_TRIPCOUNT min=4 max=4
        count_sum += ap_uint<CountBits + 1>(counts[packet]);
        busy_sum += ap_uint<BusySumBits + 1>(counts[packet]) *
                    ap_uint<BusySumBits + 1>(service[packet]);
        second_sum += ap_uint<ServiceSecondSumBits + 1>(counts[packet]) *
                      ap_uint<ServiceSecondSumBits + 1>(square[packet]);
        ++packet;
    }
    if (count_sum[CountBits]) {
        r0 = ~Count(0);
        saturated = 1;
    } else {
        r0 = count_sum.range(CountBits - 1, 0);
    }
    if (busy_sum[BusySumBits]) {
        r1 = ~BusySum(0);
        saturated = 1;
    } else {
        r1 = busy_sum.range(BusySumBits - 1, 0);
    }
    if (second_sum[ServiceSecondSumBits]) {
        r2 = ~ServiceSecondSum(0);
        saturated = 1;
    } else {
        r2 = second_sum.range(ServiceSecondSumBits - 1, 0);
    }
}

static TotalCostQ
fixedCost(const Count counts[MaxDomains][PacketClasses],
          const FixedLatencyQ latency[MaxDomains][PacketClasses],
          DomainCount domains, ap_uint<1> &saturated)
{
#pragma HLS INLINE off
    ap_uint<TotalCostBits + 1> total = 0;
    unsigned item = 0;
    const unsigned items = domains * PacketClasses;
    while (item < items) {
#pragma HLS LOOP_TRIPCOUNT min=4 max=32
        const unsigned domain = item / PacketClasses;
        const unsigned packet = item % PacketClasses;
        total += ap_uint<TotalCostBits + 1>(counts[domain][packet]) *
                 latency[domain][packet];
        ++item;
    }
    if (total[TotalCostBits]) {
        saturated = 1;
        return ~TotalCostQ(0);
    }
    return TotalCostQ(total);
}

void
uacc_allocator(
    DomainCount domain_count,
    CandidateCount candidate_count,
    const ArrivalCount interarrival_samples[MaxDomains],
    const ArrivalSum interarrival_sum[MaxDomains],
    const ArrivalSquareSum interarrival_square_sum[MaxDomains],
    const WaitSum observed_wait_sum[MaxDomains],
    const Occupancy occupancy_max[MaxDomains],
    const EventCount buffer_full_count[MaxDomains],
    const EventCount backpressure_count[MaxDomains],
    const ap_uint<1> collector_overflow[MaxDomains],
    const FactorQ previous_ca2_q[MaxDomains],
    const FactorQ previous_beta_q[MaxDomains],
    const Count candidate_class_count
        [MaxCandidates][MaxDomains][PacketClasses],
    const ServiceCycles service_cycles[MaxDomains][PacketClasses],
    const ServiceSquare service_square[MaxDomains][PacketClasses],
    const FixedLatencyQ fixed_latency_q[MaxDomains][PacketClasses],
    const TotalCostQ capacity_gain_q[MaxCandidates],
    const LookaheadDelta lookahead_delta[MaxCandidates],
    CycleCount window_cycles,
    UtilizationQ rho_max_q,
    FactorQ ca2_max_q,
    FactorQ beta_max_q,
    ap_uint<4> ca2_ewma_shift,
    ap_uint<4> beta_ewma_shift,
    Occupancy occupancy_threshold,
    EventCount backpressure_threshold,
    FactorQ updated_ca2_q[MaxDomains],
    FactorQ updated_beta_q[MaxDomains],
    TotalCostQ candidate_queue_cost_q[MaxCandidates],
    TotalCostQ candidate_fixed_cost_q[MaxCandidates],
    SignedTotalCostQ candidate_mu_q[MaxCandidates],
    SignedTotalCostQ candidate_score_q[MaxCandidates],
    ap_uint<1> candidate_valid[MaxCandidates],
    CandidateId &best_candidate,
    ap_uint<1> &best_candidate_found,
    ap_uint<1> &arithmetic_saturated)
{
#pragma HLS INTERFACE ap_ctrl_hs port=return
#pragma HLS ALLOCATION instances=uacc_cost_core limit=1 function
#pragma HLS ALLOCATION instances=sharedWideDivide limit=1 function

    const FactorQ one_q = FactorQ(1) << FractionBits;
    ap_uint<1> domain_guard[MaxDomains];
    ap_uint<TotalCostBits + 1> queue_accumulator[MaxCandidates];
    ap_uint<1> valid_accumulator[MaxCandidates];
#pragma HLS RESOURCE variable=queue_accumulator core=RAM_1P_LUTRAM
    arithmetic_saturated = 0;
    best_candidate = 0;
    best_candidate_found = 0;

    for (unsigned domain = 0; domain < MaxDomains; ++domain) {
        updated_ca2_q[domain] = previous_ca2_q[domain];
        updated_beta_q[domain] = previous_beta_q[domain];
        domain_guard[domain] = 0;
        if (domain < domain_count) {
            updated_ca2_q[domain] = arrivalCa2(
                interarrival_samples[domain], interarrival_sum[domain],
                interarrival_square_sum[domain], previous_ca2_q[domain],
                ca2_max_q, ca2_ewma_shift);
        }
    }

    for (unsigned candidate = 0; candidate < MaxCandidates; ++candidate) {
        candidate_queue_cost_q[candidate] = 0;
        candidate_fixed_cost_q[candidate] = 0;
        candidate_mu_q[candidate] = 0;
        candidate_score_q[candidate] = 0;
        candidate_valid[candidate] = 0;
        queue_accumulator[candidate] = 0;
        valid_accumulator[candidate] = candidate < candidate_count;
    }

    unsigned phase = 0;
    const unsigned phases = candidate_count + 1;
    while (phase < phases) {
#pragma HLS LOOP_TRIPCOUNT min=2 max=14
        unsigned domain = 0;
        while (domain < domain_count) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=8
            const bool current_phase = phase == 0;
            const unsigned candidate = current_phase ? 0 : phase - 1;
            Count r0;
            BusySum r1;
            ServiceSecondSum r2;
            ap_uint<1> traffic_saturated = 0;
            trafficSums(
                candidate_class_count[candidate][domain],
                service_cycles[domain], service_square[domain],
                r0, r1, r2, traffic_saturated);
            FactorQ effective_ca2 = updated_ca2_q[domain];
            if (effective_ca2 < one_q)
                effective_ca2 = one_q;
            const FactorQ beta = current_phase ? one_q :
                updated_beta_q[domain];
            CostQ model_cost;
            CostQ adjusted_cost;
            const ap_uint<2> queue_status = uacc_cost_core(
                r0, r1, r2, effective_ca2, beta, window_cycles,
                rho_max_q, model_cost, adjusted_cost);
            const ap_uint<1> queue_valid = queue_status[0];
            const ap_uint<1> queue_saturated = queue_status[1];

            if (current_phase) {
                updated_beta_q[domain] = feedbackBeta(
                    observed_wait_sum[domain], model_cost,
                    previous_beta_q[domain], beta_max_q,
                    beta_ewma_shift);
                const ap_uint<EventCountBits + 1> pressure =
                    ap_uint<EventCountBits + 1>(
                        buffer_full_count[domain]) +
                    backpressure_count[domain];
                const bool occupancy_ok = occupancy_threshold == 0 ||
                    occupancy_max[domain] <= occupancy_threshold;
                const bool pressure_ok = backpressure_threshold == 0 ||
                    pressure <= backpressure_threshold;
                domain_guard[domain] = queue_valid && occupancy_ok &&
                    pressure_ok && !collector_overflow[domain] &&
                    !traffic_saturated && !queue_saturated;
            } else {
                queue_accumulator[candidate] += adjusted_cost;
                valid_accumulator[candidate] &= queue_valid &&
                    domain_guard[domain] && !traffic_saturated &&
                    !queue_saturated;
            }
            arithmetic_saturated |= traffic_saturated | queue_saturated;
            ++domain;
        }
        ++phase;
    }

    unsigned candidate = 0;
    while (candidate < candidate_count) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=13
        ap_uint<1> candidate_saturated = 0;
        if (queue_accumulator[candidate][TotalCostBits]) {
            candidate_queue_cost_q[candidate] = ~TotalCostQ(0);
            candidate_saturated = 1;
        } else {
            candidate_queue_cost_q[candidate] =
                TotalCostQ(queue_accumulator[candidate]);
        }
        candidate_fixed_cost_q[candidate] = fixedCost(
            candidate_class_count[candidate], fixed_latency_q,
            domain_count, candidate_saturated);
        candidate_valid[candidate] = valid_accumulator[candidate] &&
            !candidate_saturated;
        arithmetic_saturated |= candidate_saturated;
        ++candidate;
    }

    SignedTotalCostQ best_score = 0;
    if (candidate_count != 0 && candidate_valid[0]) {
        for (unsigned candidate = 1; candidate < MaxCandidates; ++candidate) {
            if (candidate < candidate_count && candidate_valid[candidate]) {
                SignedTotalCostQ mu =
                    SignedTotalCostQ(capacity_gain_q[candidate]) -
                    SignedTotalCostQ(capacity_gain_q[0]) -
                    (SignedTotalCostQ(candidate_fixed_cost_q[candidate]) -
                     SignedTotalCostQ(candidate_fixed_cost_q[0])) -
                    (SignedTotalCostQ(candidate_queue_cost_q[candidate]) -
                     SignedTotalCostQ(candidate_queue_cost_q[0]));
                candidate_mu_q[candidate] = mu;
                SignedTotalCostQ score = 0;
                if (lookahead_delta[candidate] == 1)
                    score = mu;
                else if (lookahead_delta[candidate] == 2)
                    score = mu >> 1;
                else if (lookahead_delta[candidate] == 4)
                    score = mu >> 2;
                else
                    candidate_valid[candidate] = 0;
                candidate_score_q[candidate] = score;
                if (candidate_valid[candidate] && score > best_score) {
                    best_score = score;
                    best_candidate = candidate;
                    best_candidate_found = 1;
                }
            }
        }
    }
}
