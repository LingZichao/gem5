#include "uacc_section14_cost.hpp"

using namespace uacc_hls;

// Midpoint reciprocals for 32 bins over [1, 2), followed by one Newton step.
static const ap_uint<16> ReciprocalLutQ16[32] = {
    64528, 62602, 60787, 59075, 57456, 55924, 54471, 53092,
    51782, 50534, 49345, 48210, 47127, 46091, 45100, 44151,
    43240, 42367, 41528, 40721, 39946, 39199, 38480, 37787,
    37118, 36472, 35849, 35246, 34664, 34100, 33554, 33026,
};

static SharedDividerQ
reciprocalDivide(
    SharedDividerQ numerator, ap_uint<WindowBits + 1> denominator)
{
#pragma HLS INLINE
#pragma HLS RESOURCE variable=ReciprocalLutQ16 core=ROM_1P_LUTRAM
    const unsigned ReciprocalBits = 16;

    ap_uint<8> exponent = 0;
    ap_uint<1> found = 0;
    for (int bit = WindowBits; bit >= 0; --bit) {
#pragma HLS UNROLL
        if (!found && denominator[bit]) {
            exponent = bit;
            found = 1;
        }
    }

    ap_uint<18> normalized_q;
    if (exponent >= ReciprocalBits)
        normalized_q = denominator >> (exponent - ReciprocalBits);
    else
        normalized_q = denominator << (ReciprocalBits - exponent);

    const ap_uint<5> lut_index = normalized_q.range(15, 11);
    ap_uint<16> reciprocal_q = ReciprocalLutQ16[lut_index];
    const ap_uint<34> product_q =
        ap_uint<34>(normalized_q) * reciprocal_q;
    const ap_uint<18> correction_q =
        (ap_uint<18>(2) << ReciprocalBits) -
        (product_q >> ReciprocalBits);
    reciprocal_q =
        (ap_uint<34>(reciprocal_q) * correction_q) >>
        ReciprocalBits;

    const ap_uint<SharedDividerBits + 16> scaled =
        ap_uint<SharedDividerBits + 16>(numerator) * reciprocal_q;
    return scaled >> (ReciprocalBits + exponent);
}

#ifndef UACC_HLS_CORE_ONLY
void
uacc_section14_cost(
    Count r0,
    BusySum r1,
    ServiceSecondSum r2,
    FactorQ ca2_q,
    FactorQ beta_q,
    CycleCount window_cycles,
    UtilizationQ rho_max_q,
    CostQ &model_cost_q,
    CostQ &adjusted_cost_q,
    ap_uint<1> &valid,
    ap_uint<1> &saturated)
{
#pragma HLS INTERFACE ap_ctrl_hs port=return
#pragma HLS INTERFACE ap_none port=r0
#pragma HLS INTERFACE ap_none port=r1
#pragma HLS INTERFACE ap_none port=r2
#pragma HLS INTERFACE ap_none port=ca2_q
#pragma HLS INTERFACE ap_none port=beta_q
#pragma HLS INTERFACE ap_none port=window_cycles
#pragma HLS INTERFACE ap_none port=rho_max_q
#pragma HLS INTERFACE ap_none port=model_cost_q
#pragma HLS INTERFACE ap_none port=adjusted_cost_q
#pragma HLS INTERFACE ap_none port=valid
#pragma HLS INTERFACE ap_none port=saturated

    const ap_uint<2> status = uacc_section14_cost_core(
        r0, r1, r2, ca2_q, beta_q, window_cycles, rho_max_q,
        model_cost_q, adjusted_cost_q);
    valid = status[0];
    saturated = status[1];
}
#endif

ap_uint<2>
uacc_section14_cost_core(
    Count r0,
    BusySum r1,
    ServiceSecondSum r2,
    FactorQ ca2_q,
    FactorQ beta_q,
    CycleCount window_cycles,
    UtilizationQ rho_max_q,
    CostQ &model_cost_q,
    CostQ &adjusted_cost_q)
{
#pragma HLS INLINE off

    const FactorQ one_q = FactorQ(1) << FractionBits;
    constexpr unsigned UtilizationCompareBits =
        BusySumBits + RhoFractionBits + 1;
    const ap_uint<UtilizationCompareBits> offered_q =
        ap_uint<UtilizationCompareBits>(r1) << RhoFractionBits;
    const ap_uint<UtilizationCompareBits> limit_q =
        ap_uint<UtilizationCompareBits>(window_cycles) *
        ap_uint<UtilizationCompareBits>(rho_max_q);

    model_cost_q = 0;
    adjusted_cost_q = 0;
    ap_uint<1> valid = 0;
    ap_uint<1> saturated = 0;

    if (window_cycles == 0 || r1 >= window_cycles ||
        offered_q >= limit_q) {
        valid = 0;
    } else {
        valid = 1;

        FactorQ ca2_minus_one = 0;
        if (ca2_q > one_q)
            ca2_minus_one = ca2_q - one_q;
        FactorQ effective_beta = one_q;
        if (beta_q >= one_q)
            effective_beta = beta_q;

        const CycleCount guarded_busy = CycleCount(r1);
        const SharedDividerQ busy_square =
            SharedDividerQ(guarded_busy) * SharedDividerQ(guarded_busy);
        const SharedDividerQ burst_term_q =
            busy_square * SharedDividerQ(ca2_minus_one);
        const SharedDividerQ service_term_q =
            (SharedDividerQ(r0) * SharedDividerQ(r2)) << FractionBits;
        const SharedDividerQ numerator_q =
            burst_term_q + service_term_q;
        const ap_uint<WindowBits + 1> denominator =
            ap_uint<WindowBits + 1>(window_cycles - guarded_busy) << 1;
        const SharedDividerQ quotient_q =
            reciprocalDivide(numerator_q, denominator);

        if ((quotient_q >> CostBits) != 0) {
            model_cost_q = ~CostQ(0);
            saturated = 1;
        } else {
            model_cost_q = CostQ(quotient_q);
        }

        const ap_uint<CostBits + FactorBits> adjusted_product =
            ap_uint<CostBits + FactorBits>(model_cost_q) *
            ap_uint<CostBits + FactorBits>(effective_beta);
        if ((adjusted_product >> (CostBits + FractionBits)) != 0) {
            adjusted_cost_q = ~CostQ(0);
            saturated = 1;
        } else {
            adjusted_cost_q = CostQ(adjusted_product >> FractionBits);
        }
    }
    ap_uint<2> status = 0;
    status[0] = valid;
    status[1] = saturated;
    return status;
}
