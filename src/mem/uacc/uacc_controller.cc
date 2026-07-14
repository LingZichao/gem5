#include "mem/uacc/uacc_controller.hh"

#include <algorithm>
#include <cmath>
#include <limits>

#include "base/logging.hh"
#include "debug/UACC.hh"
#include "mem/uacc/uacc_partition.hh"
#include "sim/core.hh"

namespace gem5
{

UACCController::UACCStats::UACCStats(UACCController &controller)
    : statistics::Group(&controller),
      ADD_STAT(profileWindows, statistics::units::Count::get(),
               "Number of completed UACC profiling windows"),
      ADD_STAT(localMisses, statistics::units::Count::get(),
               "Demand misses observed by UACC selectors"),
      ADD_STAT(localCacheHits, statistics::units::Count::get(),
               "Requests served by the host-side UACC local cache"),
      ADD_STAT(remoteLookups, statistics::units::Count::get(),
               "Requests sent through a UACC remote path"),
      ADD_STAT(remoteHits, statistics::units::Count::get(),
               "Requests hitting in the remote UACC cache"),
      ADD_STAT(remoteMisses, statistics::units::Count::get(),
               "Requests missing in the remote UACC cache"),
      ADD_STAT(atomicSwapProbes, statistics::units::Count::get(),
               "Host/UACC probes eligible for atomic swap"),
      ADD_STAT(atomicSwapHits, statistics::units::Count::get(),
               "Atomic-swap probes finding a remote line"),
      ADD_STAT(atomicSwaps, statistics::units::Count::get(),
               "Completed host/remote atomic line exchanges"),
      ADD_STAT(allocationChanges, statistics::units::Count::get(),
               "Number of profiling windows changing allocation"),
      ADD_STAT(allocationWays, statistics::units::Count::get(),
               "Current remote way allocation per core"),
      ADD_STAT(lastUtility, statistics::units::Count::get(),
               "Last selected marginal utility per core")
{
    allocationWays.init(controller.numCores);
    lastUtility.init(controller.numCores);
    allocationWays.flags(statistics::nozero | statistics::oneline);
    lastUtility.flags(statistics::oneline);
}

UACCController::UACCController(const Params &p)
    : ClockedObject(p),
      numCores(p.num_cores),
      profileInterval(p.profile_interval),
      sampledSets(std::max(1u, p.sampled_sets)),
      profileDepth(std::max(1u, p.profile_depth)),
      baseWays(p.base_ways),
      maxRemoteWays(p.max_remote_ways),
      cacheLineSize(std::max(1u, p.cache_line_size)),
      lowerMissPenalty(p.lower_miss_penalty_cycles),
      distanceDiscountPerNs(p.distance_discount_per_ns),
      d2dRtt(p.d2d_rtt),
      d2dBandwidth(p.d2d_bandwidth),
      responseFlits(std::max(1u, p.response_flits)),
      allocationPolicy(p.allocation_policy),
      dynamicAllocation(p.dynamic_allocation),
      lookaheadDeltas(p.lookahead_deltas),
      distanceNs(p.distance_ns),
      initialAllocation(p.initial_allocation),
      partitionManager(p.partition_manager),
      cores(numCores),
      allocations(numCores, 0),
      profileEvent([this]{ profileTick(); }, name()),
      stats(*this)
{
    fatal_if(numCores == 0, "UACCController requires at least one core");
    fatal_if(profileInterval == 0, "UACC profile_interval must be non-zero");
    fatal_if(allocationPolicy != "static" &&
             allocationPolicy != "greedy" &&
             allocationPolicy != "distance" &&
             allocationPolicy != "congestion",
             "Unknown UACC allocation policy '%s'", allocationPolicy);

    for (auto &core : cores) {
        core.atd.resize(sampledSets);
        core.reuseHistogram.resize(profileDepth + 1, 0);
    }

    unsigned remaining = maxRemoteWays;
    for (unsigned i = 0; i < numCores && i < initialAllocation.size(); ++i) {
        allocations[i] = std::min(initialAllocation[i], remaining);
        remaining -= allocations[i];
    }
}

void
UACCController::startup()
{
    applyAllocation(allocations, std::vector<double>(numCores, 0.0));
    if (dynamicAllocation && allocationPolicy != "static")
        schedule(profileEvent, clockEdge(profileInterval));
}

unsigned
UACCController::allocation(unsigned coreId) const
{
    return coreId < allocations.size() ? allocations[coreId] : 0;
}

bool
UACCController::remoteEnabled(unsigned coreId, PacketPtr pkt) const
{
    if (coreId >= allocations.size() || allocations[coreId] == 0)
        return false;
    if (!pkt || !pkt->req || pkt->req->isUncacheable())
        return false;
    return true;
}

void
UACCController::recordLocalMiss(unsigned coreId, Addr addr)
{
    if (coreId >= cores.size()) {
        return;
    }

    auto &core = cores[coreId];
    const Addr line = addr / cacheLineSize;
    const unsigned set = line % sampledSets;
    auto &lru = core.atd[set];

    auto it = std::find(lru.begin(), lru.end(), line);
    if (it != lru.end()) {
        const unsigned position = std::distance(lru.begin(), it);
        if (position < core.reuseHistogram.size())
            ++core.reuseHistogram[position];
        lru.erase(it);
    }

    lru.push_front(line);
    if (lru.size() > profileDepth)
        lru.pop_back();

    ++core.localMisses;
    ++stats.localMisses;
}

void
UACCController::recordLocalCacheHit(unsigned coreId)
{
    if (coreId < cores.size())
        ++stats.localCacheHits;
}

void
UACCController::recordRemoteLookup(unsigned coreId)
{
    if (coreId < cores.size()) {
        ++cores[coreId].remoteLookups;
        ++stats.remoteLookups;
    }
}

void
UACCController::recordRemoteAccess(unsigned coreId, bool hit)
{
    if (coreId >= cores.size())
        return;

    if (hit) {
        ++cores[coreId].remoteHits;
        ++stats.remoteHits;
    } else {
        ++stats.remoteMisses;
    }
}

void
UACCController::recordAtomicSwapProbe(unsigned coreId)
{
    if (coreId < cores.size())
        ++stats.atomicSwapProbes;
}

void
UACCController::recordAtomicSwapHit(unsigned coreId)
{
    if (coreId < cores.size())
        ++stats.atomicSwapHits;
}

void
UACCController::recordAtomicSwap(unsigned coreId)
{
    if (coreId < cores.size())
        ++stats.atomicSwaps;
}

uint64_t
UACCController::histogramCount(const CoreState &core, unsigned begin,
                               unsigned end) const
{
    begin = std::min(begin, static_cast<unsigned>(core.reuseHistogram.size()));
    end = std::min(end, static_cast<unsigned>(core.reuseHistogram.size()));
    if (begin >= end)
        return 0;

    uint64_t result = 0;
    for (unsigned i = begin; i < end; ++i)
        result += core.reuseHistogram[i];
    return result;
}

double
UACCController::remoteProbability(const CoreState &core, unsigned ways) const
{
    if (core.localMisses == 0)
        return 0.0;
    return static_cast<double>(histogramCount(
        core, baseWays, baseWays + ways)) / core.localMisses;
}

double
UACCController::queueWaitCycles(double lambda) const
{
    if (lambda <= 0.0 || d2dBandwidth <= 0.0)
        return 0.0;

    // MemoryBandwidth::getValue() converts a bandwidth into ticks per byte.
    // Therefore serialization is already expressed in simulator ticks; it
    // must not be computed as bits divided by d2dBandwidth.
    const double serializationTicks =
        static_cast<double>(cacheLineSize) * d2dBandwidth;
    const double serviceTicks = std::max(
        serializationTicks, static_cast<double>(d2dRtt));
    const double serviceSeconds = serviceTicks / sim_clock::as_float::s;
    const double rho = lambda * serviceSeconds;

    if (rho >= 0.999)
        return std::numeric_limits<double>::max() / 4.0;

    const double waitSeconds = lambda * serviceSeconds * serviceSeconds /
        (2.0 * (1.0 - rho));
    return waitSeconds * sim_clock::as_float::s /
        static_cast<double>(clockPeriod());
}

void
UACCController::profileTick()
{
    runAllocator();
    schedule(profileEvent, clockEdge(profileInterval));
}

void
UACCController::runAllocator()
{
    if (allocationPolicy == "static")
        return;

    std::vector<unsigned> next(numCores, 0);
    std::vector<double> utilities(numCores, 0.0);

    for (unsigned coreId = 0; coreId < numCores; ++coreId) {
        DPRINTF(UACC, "allocator core %u: misses=%llu hist[0]=%llu "
                "hist[1]=%llu hist[2]=%llu\n", coreId,
                cores[coreId].localMisses,
                cores[coreId].reuseHistogram.size() > 0 ?
                    cores[coreId].reuseHistogram[0] : 0,
                cores[coreId].reuseHistogram.size() > 1 ?
                    cores[coreId].reuseHistogram[1] : 0,
                cores[coreId].reuseHistogram.size() > 2 ?
                    cores[coreId].reuseHistogram[2] : 0);
    }

    const double intervalSeconds =
        static_cast<double>(profileInterval) * clockPeriod() /
        sim_clock::as_float::s;
    const double safeInterval = std::max(intervalSeconds, 1e-30);
    double lambda = 0.0;
    for (const auto &core : cores)
        lambda += core.remoteLookups * responseFlits / safeInterval;

    unsigned allocated = 0;
    while (allocated < maxRemoteWays) {
        double bestUtility = 0.0;
        unsigned bestCore = numCores;

        for (unsigned coreId = 0; coreId < numCores; ++coreId) {
            const auto &state = cores[coreId];
            if (state.localMisses == 0 || next[coreId] >= maxRemoteWays)
                continue;

            for (const unsigned delta : lookaheadDeltas) {
                if (delta == 0 || allocated + delta > maxRemoteWays)
                    continue;

                const unsigned current = next[coreId];
                const double missReduction = static_cast<double>(
                    histogramCount(state, baseWays + current,
                                   baseWays + current + delta)) /
                    state.localMisses;
                const double distance = coreId < distanceNs.size() ?
                    distanceNs[coreId] : 0.0;
                const double discount = allocationPolicy == "greedy" ? 1.0 :
                    std::max(0.0, 1.0 - distance * distanceDiscountPerNs);
                const double gain = missReduction * lowerMissPenalty *
                    discount;

                const double oldRemote = remoteProbability(state, current);
                const double newRemote = remoteProbability(state,
                                                            current + delta);
                const double missRate = state.localMisses / safeInterval;
                const double deltaLambda =
                    missRate * (newRemote - oldRemote) * responseFlits;
                const double congestion =
                    allocationPolicy == "greedy" ||
                    allocationPolicy == "distance" ? 0.0 :
                    queueWaitCycles(lambda + deltaLambda) -
                    queueWaitCycles(lambda);
                const double utility = gain - congestion;

                if (utility > bestUtility) {
                    bestUtility = utility;
                    bestCore = coreId;
                }
            }
        }

        if (bestCore == numCores)
            break;

        ++next[bestCore];
        ++allocated;
        utilities[bestCore] = bestUtility;

        DPRINTF(UACC, "allocator selected core %u utility=%f ways=%u\n",
                bestCore, bestUtility, next[bestCore]);

        const auto &state = cores[bestCore];
        const double oldRemote = remoteProbability(state, next[bestCore] - 1);
        const double newRemote = remoteProbability(state, next[bestCore]);
        lambda += state.localMisses / safeInterval *
            (newRemote - oldRemote) * responseFlits;
    }

    applyAllocation(next, utilities);
    ++stats.profileWindows;
    resetWindow();
}

void
UACCController::applyAllocation(const std::vector<unsigned> &newAllocation,
                                const std::vector<double> &utilities)
{
    bool changed = false;
    unsigned remaining = maxRemoteWays;
    for (unsigned i = 0; i < numCores; ++i) {
        const unsigned value = i < newAllocation.size() ?
            std::min(newAllocation[i], remaining) : 0;
        remaining -= value;
        changed |= value != allocations[i];
        allocations[i] = value;
        stats.allocationWays[i] = value;
        stats.lastUtility[i] = i < utilities.size() ? utilities[i] : 0.0;
        if (partitionManager)
            partitionManager->setAllocation(i, value);
    }
    if (changed)
        ++stats.allocationChanges;
}

void
UACCController::resetWindow()
{
    for (auto &core : cores) {
        std::fill(core.reuseHistogram.begin(), core.reuseHistogram.end(), 0);
        core.localMisses = 0;
        core.remoteLookups = 0;
        core.remoteHits = 0;
    }
}

} // namespace gem5
