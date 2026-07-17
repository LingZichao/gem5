#include "uacc_allocator.hpp"

#include <iostream>

namespace
{

using namespace uacc_hls;

constexpr uint64_t OneQ = uint64_t(1) << FractionBits;
constexpr uint64_t RhoOneQ = uint64_t(1) << RhoFractionBits;
constexpr uint64_t RhoMaxQ = 9 * RhoOneQ / 10;

struct Outputs
{
    FactorQ ca2[MaxDomains];
    FactorQ beta[MaxDomains];
    TotalCostQ queue[MaxCandidates];
    TotalCostQ fixed[MaxCandidates];
    SignedTotalCostQ mu[MaxCandidates];
    SignedTotalCostQ score[MaxCandidates];
    ap_uint<1> valid[MaxCandidates];
    CandidateId best;
    ap_uint<1> found;
    ap_uint<1> saturated;
};

struct Inputs
{
    ArrivalCount samples[MaxDomains];
    ArrivalSum sum[MaxDomains];
    ArrivalSquareSum square_sum[MaxDomains];
    WaitSum wait[MaxDomains];
    Occupancy occupancy[MaxDomains];
    EventCount full[MaxDomains];
    EventCount backpressure[MaxDomains];
    ap_uint<1> overflow[MaxDomains];
    FactorQ ca2[MaxDomains];
    FactorQ beta[MaxDomains];
    Count counts[MaxCandidates][MaxDomains][PacketClasses];
    ServiceCycles service[MaxDomains][PacketClasses];
    ServiceSquare service_square[MaxDomains][PacketClasses];
    FixedLatencyQ fixed_latency[MaxDomains][PacketClasses];
    TotalCostQ gain[MaxCandidates];
    LookaheadDelta delta[MaxCandidates];
};

void
clear(Inputs &input)
{
    for (unsigned domain = 0; domain < MaxDomains; ++domain) {
        input.samples[domain] = 0;
        input.sum[domain] = 0;
        input.square_sum[domain] = 0;
        input.wait[domain] = 0;
        input.occupancy[domain] = 0;
        input.full[domain] = 0;
        input.backpressure[domain] = 0;
        input.overflow[domain] = 0;
        input.ca2[domain] = 0;
        input.beta[domain] = 0;
        for (unsigned packet = 0; packet < PacketClasses; ++packet) {
            input.service[domain][packet] = 0;
            input.service_square[domain][packet] = 0;
            input.fixed_latency[domain][packet] = 0;
        }
    }
    for (unsigned candidate = 0; candidate < MaxCandidates; ++candidate) {
        input.gain[candidate] = 0;
        input.delta[candidate] = 0;
        for (unsigned domain = 0; domain < MaxDomains; ++domain) {
            for (unsigned packet = 0; packet < PacketClasses; ++packet)
                input.counts[candidate][domain][packet] = 0;
        }
    }
}

Outputs
run(const Inputs &input, Occupancy occupancy_threshold = 0,
    EventCount pressure_threshold = 0)
{
    Outputs output{};
    uacc_allocator(
        1, 3, input.samples, input.sum, input.square_sum, input.wait,
        input.occupancy, input.full, input.backpressure, input.overflow,
        input.ca2, input.beta, input.counts, input.service,
        input.service_square, input.fixed_latency, input.gain, input.delta,
        100, RhoMaxQ, 8 * OneQ, 4 * OneQ, 2, 2,
        occupancy_threshold, pressure_threshold, output.ca2, output.beta,
        output.queue, output.fixed, output.mu, output.score, output.valid,
        output.best, output.found, output.saturated);
    return output;
}

bool
expect(bool condition, const char *message)
{
    if (!condition)
        std::cerr << message << '\n';
    return condition;
}

} // anonymous namespace

int
main()
{
    Inputs input;
    clear(input);
    input.samples[0] = 2;
    input.sum[0] = 20;
    input.square_sum[0] = 200;
    input.wait[0] = 5;
    input.ca2[0] = OneQ;
    input.beta[0] = OneQ;
    input.service[0][0] = 10;
    input.service_square[0][0] = 100;
    input.fixed_latency[0][0] = OneQ;
    input.counts[0][0][0] = 2;
    input.counts[1][0][0] = 3;
    input.counts[2][0][0] = 4;
    input.gain[1] = 10 * OneQ;
    input.gain[2] = 5 * OneQ;
    input.delta[1] = 1;
    input.delta[2] = 2;

    bool ok = true;
    const Outputs normal = run(input);
    ok &= expect(normal.ca2[0] == 3 * OneQ / 4, "CA2 shift-EWMA");
    ok &= expect(normal.beta[0] == 5 * OneQ / 4,
                 "beta shift-EWMA");
    ok &= expect(normal.valid[0] && normal.valid[1] && normal.valid[2],
                 "candidate validity");
    ok &= expect(normal.queue[1] > normal.queue[0] &&
                 normal.queue[2] > normal.queue[1],
                 "queue cost monotonicity");
    ok &= expect(normal.fixed[0] == 2 * OneQ &&
                 normal.fixed[1] == 3 * OneQ,
                 "fixed cost");
    ok &= expect(normal.mu[1] > 0 && normal.mu[2] < 0,
                 "marginal utility signs");
    ok &= expect(normal.found && normal.best == 1,
                 "highest positive score selection");
    ok &= expect(!normal.saturated, "normal arithmetic saturation");

#if UACC_HLS_RANGE_PROFILE == 16
    Inputs lossy_overflow;
    clear(lossy_overflow);
    lossy_overflow.service[0][0] = 32;
    lossy_overflow.service_square[0][0] = 1024;
    lossy_overflow.counts[0][0][0] = 3000;
    const Outputs lossy_saturated = run(lossy_overflow);
    ok &= expect(lossy_saturated.saturated,
                 "lossy profile reports traffic-sum overflow");
    ok &= expect(!lossy_saturated.valid[0],
                 "lossy profile rejects overflowed candidate");
#endif

    input.occupancy[0] = 5;
    const Outputs guarded = run(input, 4, 0);
    ok &= expect(!guarded.valid[0] && !guarded.found,
                 "occupancy guard");
    input.occupancy[0] = 0;

    input.full[0] = 2;
    input.backpressure[0] = 2;
    const Outputs pressured = run(input, 0, 3);
    ok &= expect(!pressured.valid[0] && !pressured.found,
                 "backpressure guard");
    input.full[0] = 0;
    input.backpressure[0] = 0;

    input.counts[1][0][0] = 10;
    const Outputs utilization = run(input);
    ok &= expect(!utilization.valid[1] && utilization.best != 1,
                 "utilization guard");
    input.counts[1][0][0] = 3;

    input.delta[1] = 3;
    const Outputs bad_delta = run(input);
    ok &= expect(!bad_delta.valid[1] && !bad_delta.found,
                 "unsupported lookahead delta");
    input.delta[1] = 1;

    input.samples[0] = 0;
    input.ca2[0] = 2 * OneQ;
    const Outputs no_samples = run(input);
    ok &= expect(no_samples.ca2[0] == 2 * OneQ,
                 "empty window preserves CA2");

    input.samples[0] = 1;
    input.sum[0] = 0;
    input.square_sum[0] = 0;
    input.ca2[0] = OneQ;
    const Outputs zero_sum = run(input);
    ok &= expect(zero_sum.ca2[0] == 11 * OneQ / 4,
                 "zero arrival sum uses CA2 maximum");

    input.samples[0] = 2;
    input.sum[0] = 20;
    input.square_sum[0] = 200;
    input.wait[0] = 1000;
    const Outputs beta_clip = run(input);
    ok &= expect(beta_clip.beta[0] == 7 * OneQ / 4,
                 "beta maximum and shift-EWMA");

    input.wait[0] = 0;
    input.counts[0][0][0] = 0;
    input.counts[1][0][0] = 1;
    input.counts[2][0][0] = 0;
    input.gain[1] = 5 * OneQ;
    input.gain[2] = 0;
    const Outputs activation = run(input);
    ok &= expect(activation.queue[0] == 0 && activation.queue[1] > 0 &&
                 activation.fixed[0] == 0 &&
                 activation.fixed[1] == OneQ,
                 "zero-to-one-way activation traffic");
    ok &= expect(activation.found && activation.best == 1,
                 "activation candidate selection");

    // Differentially exercise the rolled wide divider through the CA2 path.
    // The optimized implementation must remain bit-exact with truncating
    // ap_uint division; only its RTL latency/resource mapping may change.
    uint64_t random_state = 0x554143434132ULL;
    for (unsigned test = 0; test < 256 && ok; ++test) {
        random_state = random_state * 6364136223846793005ULL + 1;
        Inputs random_input;
        clear(random_input);
        const uint32_t samples = 1 + (random_state & 0x3ff);
        random_state = random_state * 6364136223846793005ULL + 1;
        const uint64_t sum_limit = 2 * ProfileMaxWindowCycles;
        const uint64_t sum = 1 + (random_state % sum_limit);
        random_state = random_state * 6364136223846793005ULL + 1;
        const uint64_t square_limit =
            2 * ProfileMaxWindowCycles * ProfileMaxWindowCycles;
        const uint64_t square_sum =
            1 + (random_state % square_limit);
        random_state = random_state * 6364136223846793005ULL + 1;
        const FactorQ previous = random_state % (8 * OneQ + 1);

        random_input.samples[0] = samples;
        random_input.sum[0] = sum;
        random_input.square_sum[0] = square_sum;
        random_input.ca2[0] = previous;

        const ap_uint<160> numerator =
            (ap_uint<160>(samples) * ap_uint<160>(square_sum))
            << FractionBits;
        const ap_uint<128> denominator =
            ap_uint<128>(sum) * ap_uint<128>(sum);
        const ap_uint<160> ratio = numerator / denominator;
        const FactorQ maximum = 8 * OneQ;
        FactorQ raw = maximum;
        if (ratio <= OneQ)
            raw = 0;
        else if (ratio - OneQ < maximum)
            raw = FactorQ(ratio - OneQ);
        const ap_int<26> difference =
            ap_int<26>(raw) - ap_int<26>(previous);
        ap_int<26> expected =
            ap_int<26>(previous) + (difference >> 2);
        if (expected < 0)
            expected = 0;
        if (expected > ap_int<26>(maximum))
            expected = maximum;

        const Outputs random_output = run(random_input);
        ok &= expect(random_output.ca2[0] == FactorQ(expected),
                     "random exact CA2 division");
    }

    if (!ok)
        return 1;
    std::cout << "uacc_allocator: all tests passed\n";
    return 0;
}
