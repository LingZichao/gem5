#ifndef UACC_SECTION14_COST_HPP
#define UACC_SECTION14_COST_HPP

#include "ap_int.h"
#include "uacc_widths.hpp"

namespace uacc_hls
{

#ifndef UACC_HLS_FRACTION_BITS
#define UACC_HLS_FRACTION_BITS 8
#endif

constexpr unsigned FractionBits = UACC_HLS_FRACTION_BITS;
static_assert(FractionBits >= 1 && FractionBits <= 16,
              "UACC factor precision must be between F1 and F16");
constexpr unsigned FactorIntegerBits = 4;
constexpr unsigned FactorBits = FactorIntegerBits + FractionBits;
constexpr unsigned CostBits = CostIntegerBits + FractionBits;
constexpr unsigned RhoFractionBits =
    ProfileRhoFractionBits == 0 ? FractionBits : ProfileRhoFractionBits;
constexpr unsigned RhoBits = 1 + RhoFractionBits;
constexpr unsigned SharedDividerBits =
    SharedDividerIntegerBits + FractionBits;

using Count = ap_uint<CountBits>;
using CycleCount = ap_uint<WindowBits>;
using BusySum = ap_uint<BusySumBits>;
using ServiceSecondSum = ap_uint<ServiceSecondSumBits>;
using FactorQ = ap_uint<FactorBits>;
using UtilizationQ = ap_uint<RhoBits>;
using CostQ = ap_uint<CostBits>;
using SharedDividerQ = ap_uint<SharedDividerBits>;
using SharedDividerDenominatorQ =
    ap_uint<SharedDividerDenominatorBits>;

} // namespace uacc_hls

void uacc_cost(
    uacc_hls::Count r0,
    uacc_hls::BusySum r1,
    uacc_hls::ServiceSecondSum r2,
    uacc_hls::FactorQ ca2_q,
    uacc_hls::FactorQ beta_q,
    uacc_hls::CycleCount window_cycles,
    uacc_hls::UtilizationQ rho_max_q,
    uacc_hls::CostQ &model_cost_q,
    uacc_hls::CostQ &adjusted_cost_q,
    ap_uint<1> &valid,
    ap_uint<1> &saturated);

ap_uint<2> uacc_cost_core(
    uacc_hls::Count r0,
    uacc_hls::BusySum r1,
    uacc_hls::ServiceSecondSum r2,
    uacc_hls::FactorQ ca2_q,
    uacc_hls::FactorQ beta_q,
    uacc_hls::CycleCount window_cycles,
    uacc_hls::UtilizationQ rho_max_q,
    uacc_hls::CostQ &model_cost_q,
    uacc_hls::CostQ &adjusted_cost_q);

#endif
