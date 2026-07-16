#include "uacc_section14_cost.hpp"

#include <cstdint>
#include <iostream>
#include <random>

namespace
{

using namespace uacc_hls;

constexpr uint64_t OneQ = uint64_t(1) << FractionBits;
constexpr uint64_t RhoOneQ = uint64_t(1) << RhoFractionBits;
constexpr uint64_t RhoMaxQ = 9 * RhoOneQ / 10;

struct Result
{
    CostQ model;
    CostQ adjusted;
    bool valid;
    bool saturated;
};

Result
runHardware(uint32_t r0, uint64_t r1, uint64_t r2, uint32_t ca2,
            uint32_t beta, uint64_t window, uint32_t rho_max)
{
    Result result{};
    ap_uint<1> valid;
    ap_uint<1> saturated;
    uacc_section14_cost(
        r0, r1, r2, ca2, beta, window, rho_max, result.model,
        result.adjusted, valid, saturated);
    result.valid = valid != 0;
    result.saturated = saturated != 0;
    return result;
}

Result
reference(uint32_t r0, uint64_t r1, uint64_t r2, uint32_t ca2,
          uint32_t beta, uint64_t window, uint32_t rho_max)
{
    Result result{};
    const ap_uint<72> offered = ap_uint<72>(r1) << RhoFractionBits;
    const ap_uint<72> limit = ap_uint<72>(window) * rho_max;
    if (window == 0 || r1 >= window || offered >= limit)
        return result;

    result.valid = true;
    const uint32_t ca2_delta = ca2 > OneQ ? ca2 - OneQ : 0;
    const uint32_t effective_beta = beta >= OneQ ? beta : OneQ;
    ap_uint<128> numerator = ap_uint<128>(r1) * r1 * ca2_delta;
    numerator += (ap_uint<128>(r0) * r2) << FractionBits;
    const ap_uint<128> quotient = numerator / (2 * (window - r1));

    if ((quotient >> CostBits) != 0) {
        result.model = ~CostQ(0);
        result.saturated = true;
    } else {
        result.model = CostQ(quotient);
    }

    const ap_uint<CostBits + FactorBits> adjusted =
        ap_uint<CostBits + FactorBits>(result.model) * effective_beta;
    if ((adjusted >> (CostBits + FractionBits)) != 0) {
        result.adjusted = ~CostQ(0);
        result.saturated = true;
    } else {
        result.adjusted = CostQ(adjusted >> FractionBits);
    }
    return result;
}

bool
check(const char *name, uint32_t r0, uint64_t r1, uint64_t r2,
      uint32_t ca2, uint32_t beta, uint64_t window, uint32_t rho_max)
{
    const Result actual =
        runHardware(r0, r1, r2, ca2, beta, window, rho_max);
    const Result expected =
        reference(r0, r1, r2, ca2, beta, window, rho_max);
    const CostQ model_difference = actual.model >= expected.model ?
        actual.model - expected.model : expected.model - actual.model;
    const CostQ adjusted_difference = actual.adjusted >= expected.adjusted ?
        actual.adjusted - expected.adjusted :
        expected.adjusted - actual.adjusted;
    const CostQ model_tolerance = expected.model / 3333 + 2;
    const CostQ adjusted_tolerance = expected.adjusted / 3333 + 2;
    if (model_difference <= model_tolerance &&
        adjusted_difference <= adjusted_tolerance &&
        actual.valid == expected.valid &&
        actual.saturated == expected.saturated) {
        return true;
    }

    std::cerr << name << " failed: actual=(" << actual.model << ", "
              << actual.adjusted << ", " << actual.valid << ", "
              << actual.saturated << ") expected=(" << expected.model
              << ", " << expected.adjusted << ", " << expected.valid
              << ", " << expected.saturated << ")\n";
    return false;
}

} // anonymous namespace

int
main()
{
    bool ok = true;
    ok &= check("service-only", 2, 20, 200, OneQ, OneQ,
                100, RhoMaxQ);
    const Result service =
        runHardware(2, 20, 200, OneQ, OneQ, 100, RhoMaxQ);
    ok &= service.valid;

    ok &= check("burst-and-beta", 2, 20, 200, 2 * OneQ,
                OneQ + OneQ / 2, 100, RhoMaxQ);
    const Result burst = runHardware(
        2, 20, 200, 2 * OneQ, OneQ + OneQ / 2, 100, RhoMaxQ);
    ok &= burst.valid;

    ok &= check("ca2-clamp", 2, 20, 200, OneQ / 2, 0, 100, RhoMaxQ);
    ok &= check("rho-boundary", 1, 90, 90, OneQ, OneQ, 100, RhoMaxQ);
    ok &= !runHardware(1, 90, 90, OneQ, OneQ, 100, RhoMaxQ).valid;
    ok &= !runHardware(
        1, 100, 100, OneQ, OneQ, 100, RhoOneQ).valid;
    ok &= !runHardware(
        0, 0, 0, OneQ, OneQ, 0, RhoOneQ).valid;

    std::mt19937_64 rng(0x55414343);
    for (unsigned test = 0; test < 2000; ++test) {
        const uint64_t window = 1000 + rng() %
            (ProfileMaxWindowCycles - 999);
        const uint64_t r1 = rng() % window;
        const uint32_t r0 = rng() % ProfileMaxPacketsPerDomain;
        const uint64_t r2_limit =
            ProfileMaxPacketsPerDomain * 32ULL * 32ULL;
        const uint64_t r2 = rng() % r2_limit;
        const uint32_t ca2 = OneQ + rng() % (8 * OneQ);
        const uint32_t beta = OneQ + rng() % (3 * OneQ);
        ok &= check("random", r0, r1, r2, ca2, beta, window, RhoMaxQ);
        if (!ok)
            break;
    }

    if (!ok)
        return 1;
    std::cout << "uacc_section14_cost: all tests passed\n";
    return 0;
}
