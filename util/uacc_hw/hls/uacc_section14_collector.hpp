#ifndef UACC_SECTION14_COLLECTOR_HPP
#define UACC_SECTION14_COLLECTOR_HPP

#include "ap_int.h"
#include "uacc_section14_widths.hpp"

namespace uacc_hls
{

constexpr unsigned MaxDomains = 8;
constexpr unsigned PacketClasses = 4;

enum CollectorCommand
{
    CollectEvent = 0,
    SnapshotAndReset = 1,
    ClearAll = 2,
};

using DomainId = ap_uint<3>;
using PacketClassId = ap_uint<2>;
using ArrivalCycle = ap_uint<ArrivalCycleBits>;
using ArrivalCount = ap_uint<ArrivalCountBits>;
using ArrivalSum = ap_uint<ArrivalSumBits>;
using ArrivalSquareSum = ap_uint<ArrivalSquareSumBits>;
using WaitSum = ap_uint<WaitSumBits>;
using EventCount = ap_uint<EventCountBits>;
using Occupancy = ap_uint<16>;

} // namespace uacc_hls

void uacc_section14_collector(
    ap_uint<2> command,
    uacc_hls::DomainId domain,
    ap_uint<1> event_valid,
    uacc_hls::PacketClassId packet_class,
    uacc_hls::ArrivalCycle arrival_cycle,
    ap_uint<32> observed_wait_cycles,
    uacc_hls::Occupancy occupancy,
    ap_uint<1> buffer_full,
    ap_uint<1> backpressure,
    uacc_hls::ArrivalCount &packet_count,
    uacc_hls::ArrivalCount &interarrival_samples,
    uacc_hls::ArrivalSum &interarrival_sum,
    uacc_hls::ArrivalSquareSum &interarrival_square_sum,
    uacc_hls::ArrivalCount class_count[uacc_hls::PacketClasses],
    uacc_hls::WaitSum &observed_wait_sum,
    uacc_hls::Occupancy &occupancy_max,
    uacc_hls::EventCount &buffer_full_count,
    uacc_hls::EventCount &backpressure_count,
    ap_uint<1> &overflow);

#endif
