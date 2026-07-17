#include "uacc_collector.hpp"

#include <iostream>

namespace
{

using namespace uacc_hls;

struct Snapshot
{
    ArrivalCount packets;
    ArrivalCount samples;
    ArrivalSum sum;
    ArrivalSquareSum square_sum;
    ArrivalCount classes[PacketClasses];
    WaitSum wait;
    Occupancy occupancy;
    EventCount full;
    EventCount backpressure;
    ap_uint<1> overflow;
};

Snapshot
run(ap_uint<2> command, unsigned domain, bool valid = false,
    unsigned packet_class = 0, uint64_t arrival = 0, uint32_t wait = 0,
    unsigned occupancy = 0, bool full = false, bool backpressure = false)
{
    Snapshot snapshot{};
    uacc_collector(
        command, domain, valid, packet_class, arrival, wait, occupancy,
        full, backpressure, snapshot.packets, snapshot.samples, snapshot.sum,
        snapshot.square_sum, snapshot.classes, snapshot.wait,
        snapshot.occupancy, snapshot.full, snapshot.backpressure,
        snapshot.overflow);
    return snapshot;
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
    bool ok = true;
    run(ClearAll, 0);
    run(CollectEvent, 0, true, 0, 10, 3, 1, false, false);
    run(CollectEvent, 0, true, 1, 15, 7, 4, true, true);
    const Snapshot first = run(SnapshotAndReset, 0);
    ok &= expect(first.packets == 2, "first packet count");
    ok &= expect(first.samples == 1, "first sample count");
    ok &= expect(first.sum == 5, "first arrival sum");
    ok &= expect(first.square_sum == 25, "first square sum");
    ok &= expect(first.classes[0] == 1 && first.classes[1] == 1,
                 "first class counts");
    ok &= expect(first.wait == 10, "first wait sum");
    ok &= expect(first.occupancy == 4, "first occupancy max");
    ok &= expect(first.full == 1 && first.backpressure == 1,
                 "first pressure counters");
    ok &= expect(first.overflow == 0, "first overflow");

    run(CollectEvent, 0, true, 2, 20, 11, 2, false, false);
    const Snapshot second = run(SnapshotAndReset, 0);
    ok &= expect(second.packets == 1, "second packet count");
    ok &= expect(second.samples == 1 && second.sum == 5 &&
                 second.square_sum == 25,
                 "cross-window arrival continuity");
    ok &= expect(second.classes[2] == 1, "second class count");

    run(CollectEvent, 1, true, 3, 100, 1, 1, false, false);
    const Snapshot other = run(SnapshotAndReset, 1);
    ok &= expect(other.packets == 1 && other.samples == 0,
                 "independent domain state");

    run(ClearAll, 0);
    run(CollectEvent, 0, true, 0, 30, 0, 0, false, false);
    run(CollectEvent, 0, true, 0, 29, 0, 0, false, false);
    const Snapshot invalid = run(SnapshotAndReset, 0);
    ok &= expect(invalid.overflow == 1, "non-monotonic arrival detection");

    run(ClearAll, 0);
    run(CollectEvent, 3, false, 1, 50, 9, 7, true, true);
    const Snapshot ignored = run(SnapshotAndReset, 3);
    ok &= expect(ignored.packets == 0 && ignored.wait == 0 &&
                 ignored.full == 0 && ignored.backpressure == 0,
                 "invalid event has no side effects");

    run(CollectEvent, 3, true, 1, 60, 1, 1, false, false);
    run(ClearAll, 0);
    run(CollectEvent, 3, true, 2, 70, 2, 2, false, false);
    const Snapshot cleared = run(SnapshotAndReset, 3);
    ok &= expect(cleared.packets == 1 && cleared.samples == 0 &&
                 cleared.classes[1] == 0 && cleared.classes[2] == 1,
                 "clear all domains and arrival history");

    if (!ok)
        return 1;
    std::cout << "uacc_collector: all tests passed\n";
    return 0;
}
