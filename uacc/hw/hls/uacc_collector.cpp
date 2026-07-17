#include "uacc_collector.hpp"

using namespace uacc_hls;

template <int Width>
static ap_uint<Width>
saturatingAdd(ap_uint<Width> left, ap_uint<Width> right, ap_uint<1> &overflow)
{
    const ap_uint<Width + 1> sum =
        ap_uint<Width + 1>(left) + ap_uint<Width + 1>(right);
    if (sum[Width]) {
        overflow = 1;
        return ~ap_uint<Width>(0);
    }
    return sum.range(Width - 1, 0);
}

void
uacc_collector(
    ap_uint<2> command,
    DomainId domain,
    ap_uint<1> event_valid,
    PacketClassId packet_class,
    ArrivalCycle arrival_cycle,
    ap_uint<32> observed_wait_cycles,
    Occupancy occupancy,
    ap_uint<1> buffer_full,
    ap_uint<1> backpressure,
    ArrivalCount &packet_count,
    ArrivalCount &interarrival_samples,
    ArrivalSum &interarrival_sum,
    ArrivalSquareSum &interarrival_square_sum,
    ArrivalCount class_count[PacketClasses],
    WaitSum &observed_wait_sum,
    Occupancy &occupancy_max,
    EventCount &buffer_full_count,
    EventCount &backpressure_count,
    ap_uint<1> &overflow)
{
#pragma HLS INTERFACE ap_ctrl_hs port=return

    static ArrivalCount packet_counts[MaxDomains];
    static ArrivalCount sample_counts[MaxDomains];
    static ArrivalSum arrival_sums[MaxDomains];
    static ArrivalSquareSum arrival_square_sums[MaxDomains];
    static ArrivalCount class_counts[MaxDomains][PacketClasses];
    static WaitSum wait_sums[MaxDomains];
    static Occupancy occupancy_maxima[MaxDomains];
    static EventCount buffer_full_counts[MaxDomains];
    static EventCount backpressure_counts[MaxDomains];
    static ArrivalCycle last_arrivals[MaxDomains];
    static ap_uint<1> have_last_arrival[MaxDomains];
    static ap_uint<1> overflowed[MaxDomains];

    if (command == ClearAll) {
        for (unsigned index = 0; index < MaxDomains; ++index) {
            packet_counts[index] = 0;
            sample_counts[index] = 0;
            arrival_sums[index] = 0;
            arrival_square_sums[index] = 0;
            wait_sums[index] = 0;
            occupancy_maxima[index] = 0;
            buffer_full_counts[index] = 0;
            backpressure_counts[index] = 0;
            last_arrivals[index] = 0;
            have_last_arrival[index] = 0;
            overflowed[index] = 0;
            for (unsigned packet = 0; packet < PacketClasses; ++packet)
                class_counts[index][packet] = 0;
        }
    } else if (command == CollectEvent && event_valid) {
        ap_uint<1> event_overflow = overflowed[domain];
        packet_counts[domain] = saturatingAdd<32>(
            packet_counts[domain], ap_uint<32>(1), event_overflow);
        class_counts[domain][packet_class] = saturatingAdd<32>(
            class_counts[domain][packet_class], ap_uint<32>(1),
            event_overflow);
        wait_sums[domain] = saturatingAdd<64>(
            wait_sums[domain], ap_uint<64>(observed_wait_cycles),
            event_overflow);
        if (occupancy > occupancy_maxima[domain])
            occupancy_maxima[domain] = occupancy;
        if (buffer_full)
            buffer_full_counts[domain] = saturatingAdd<32>(
                buffer_full_counts[domain], ap_uint<32>(1), event_overflow);
        if (backpressure)
            backpressure_counts[domain] = saturatingAdd<32>(
                backpressure_counts[domain], ap_uint<32>(1),
                event_overflow);

        if (have_last_arrival[domain]) {
            if (arrival_cycle >= last_arrivals[domain]) {
                const ArrivalCycle gap =
                    arrival_cycle - last_arrivals[domain];
                const ap_uint<80> gap_square =
                    ap_uint<80>(gap) * ap_uint<80>(gap);
                sample_counts[domain] = saturatingAdd<32>(
                    sample_counts[domain], ap_uint<32>(1), event_overflow);
                arrival_sums[domain] = saturatingAdd<64>(
                    arrival_sums[domain], ap_uint<64>(gap), event_overflow);
                arrival_square_sums[domain] = saturatingAdd<96>(
                    arrival_square_sums[domain],
                    ap_uint<96>(gap_square), event_overflow);
                last_arrivals[domain] = arrival_cycle;
            } else {
                event_overflow = 1;
            }
        } else {
            last_arrivals[domain] = arrival_cycle;
            have_last_arrival[domain] = 1;
        }
        overflowed[domain] = event_overflow;
    }

    packet_count = packet_counts[domain];
    interarrival_samples = sample_counts[domain];
    interarrival_sum = arrival_sums[domain];
    interarrival_square_sum = arrival_square_sums[domain];
    observed_wait_sum = wait_sums[domain];
    occupancy_max = occupancy_maxima[domain];
    buffer_full_count = buffer_full_counts[domain];
    backpressure_count = backpressure_counts[domain];
    overflow = overflowed[domain];
    for (unsigned packet = 0; packet < PacketClasses; ++packet)
        class_count[packet] = class_counts[domain][packet];

    if (command == SnapshotAndReset) {
        packet_counts[domain] = 0;
        sample_counts[domain] = 0;
        arrival_sums[domain] = 0;
        arrival_square_sums[domain] = 0;
        wait_sums[domain] = 0;
        occupancy_maxima[domain] = 0;
        buffer_full_counts[domain] = 0;
        backpressure_counts[domain] = 0;
        overflowed[domain] = 0;
        for (unsigned packet = 0; packet < PacketClasses; ++packet)
            class_counts[domain][packet] = 0;
    }
}
