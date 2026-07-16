#ifndef UACC_SECTION14_ALLOCATOR_HPP
#define UACC_SECTION14_ALLOCATOR_HPP

#include "uacc_section14_collector.hpp"
#include "uacc_section14_cost.hpp"

namespace uacc_hls
{

constexpr unsigned MaxCandidates = 13;

using ServiceCycles = ap_uint<ServiceBits>;
using ServiceSquare = ap_uint<ServiceSquareBits>;
constexpr unsigned FixedLatencyBits =
    FixedLatencyIntegerBits + FractionBits;
constexpr unsigned TotalCostBits =
    TotalCostIntegerBits + FractionBits;
using FixedLatencyQ = ap_uint<FixedLatencyBits>;
using TotalCostQ = ap_uint<TotalCostBits>;
using SignedTotalCostQ = ap_int<TotalCostBits + 1>;
using CandidateId = ap_uint<4>;
using CandidateCount = ap_uint<4>;
using DomainCount = ap_uint<4>;
using LookaheadDelta = ap_uint<4>;

} // namespace uacc_hls

void uacc_section14_allocator(
    uacc_hls::DomainCount domain_count,
    uacc_hls::CandidateCount candidate_count,
    const uacc_hls::ArrivalCount interarrival_samples[uacc_hls::MaxDomains],
    const uacc_hls::ArrivalSum interarrival_sum[uacc_hls::MaxDomains],
    const uacc_hls::ArrivalSquareSum
        interarrival_square_sum[uacc_hls::MaxDomains],
    const uacc_hls::WaitSum observed_wait_sum[uacc_hls::MaxDomains],
    const uacc_hls::Occupancy occupancy_max[uacc_hls::MaxDomains],
    const uacc_hls::EventCount buffer_full_count[uacc_hls::MaxDomains],
    const uacc_hls::EventCount backpressure_count[uacc_hls::MaxDomains],
    const ap_uint<1> collector_overflow[uacc_hls::MaxDomains],
    const uacc_hls::FactorQ previous_ca2_q[uacc_hls::MaxDomains],
    const uacc_hls::FactorQ previous_beta_q[uacc_hls::MaxDomains],
    const uacc_hls::Count candidate_class_count
        [uacc_hls::MaxCandidates][uacc_hls::MaxDomains]
        [uacc_hls::PacketClasses],
    const uacc_hls::ServiceCycles service_cycles
        [uacc_hls::MaxDomains][uacc_hls::PacketClasses],
    const uacc_hls::ServiceSquare service_square
        [uacc_hls::MaxDomains][uacc_hls::PacketClasses],
    const uacc_hls::FixedLatencyQ fixed_latency_q
        [uacc_hls::MaxDomains][uacc_hls::PacketClasses],
    const uacc_hls::TotalCostQ
        capacity_gain_q[uacc_hls::MaxCandidates],
    const uacc_hls::LookaheadDelta
        lookahead_delta[uacc_hls::MaxCandidates],
    uacc_hls::CycleCount window_cycles,
    uacc_hls::UtilizationQ rho_max_q,
    uacc_hls::FactorQ ca2_max_q,
    uacc_hls::FactorQ beta_max_q,
    ap_uint<4> ca2_ewma_shift,
    ap_uint<4> beta_ewma_shift,
    uacc_hls::Occupancy occupancy_threshold,
    uacc_hls::EventCount backpressure_threshold,
    uacc_hls::FactorQ updated_ca2_q[uacc_hls::MaxDomains],
    uacc_hls::FactorQ updated_beta_q[uacc_hls::MaxDomains],
    uacc_hls::TotalCostQ
        candidate_queue_cost_q[uacc_hls::MaxCandidates],
    uacc_hls::TotalCostQ
        candidate_fixed_cost_q[uacc_hls::MaxCandidates],
    uacc_hls::SignedTotalCostQ
        candidate_mu_q[uacc_hls::MaxCandidates],
    uacc_hls::SignedTotalCostQ
        candidate_score_q[uacc_hls::MaxCandidates],
    ap_uint<1> candidate_valid[uacc_hls::MaxCandidates],
    uacc_hls::CandidateId &best_candidate,
    ap_uint<1> &best_candidate_found,
    ap_uint<1> &arithmetic_saturated);

#endif
