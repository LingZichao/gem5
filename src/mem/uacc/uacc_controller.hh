#ifndef __MEM_UACC_UACC_CONTROLLER_HH__
#define __MEM_UACC_UACC_CONTROLLER_HH__

#include <cstdint>
#include <list>
#include <string>
#include <vector>

#include "base/statistics.hh"
#include "base/types.hh"
#include "mem/packet.hh"
#include "params/UACCController.hh"
#include "sim/clocked_object.hh"
#include "sim/eventq.hh"

namespace gem5
{

class UACCPartitionManager;

class UACCController : public ClockedObject
{
  private:
    struct CoreState
    {
        std::vector<std::list<Addr>> atd;
        std::vector<uint64_t> reuseHistogram;
        uint64_t localMisses = 0;
        uint64_t remoteLookups = 0;
        uint64_t remoteHits = 0;
    };

    struct UACCStats : public statistics::Group
    {
        UACCStats(UACCController &controller);

        statistics::Scalar profileWindows;
        statistics::Scalar localMisses;
        statistics::Scalar localCacheHits;
        statistics::Scalar remoteLookups;
        statistics::Scalar remoteHits;
        statistics::Scalar remoteMisses;
        statistics::Scalar atomicSwapProbes;
        statistics::Scalar atomicSwapHits;
        statistics::Scalar atomicSwaps;
        statistics::Scalar allocationChanges;
        statistics::Vector allocationWays;
        statistics::Vector lastUtility;
    };

    const unsigned numCores;
    const Cycles profileInterval;
    const unsigned sampledSets;
    const unsigned profileDepth;
    const unsigned baseWays;
    const unsigned maxRemoteWays;
    const unsigned cacheLineSize;
    const double lowerMissPenalty;
    const double distanceDiscountPerNs;
    const Tick d2dRtt;
    // MemoryBandwidth parameters are represented as ticks per byte in C++.
    const double d2dBandwidth;
    const unsigned responseFlits;
    const std::string allocationPolicy;
    const bool dynamicAllocation;
    const std::vector<unsigned> lookaheadDeltas;
    const std::vector<double> distanceNs;
    const std::vector<unsigned> initialAllocation;

    UACCPartitionManager *partitionManager;
    std::vector<CoreState> cores;
    std::vector<unsigned> allocations;

    EventFunctionWrapper profileEvent;
    UACCStats stats;

    void profileTick();
    void runAllocator();
    void applyAllocation(const std::vector<unsigned> &newAllocation,
                         const std::vector<double> &utilities);
    void resetWindow();

    uint64_t histogramCount(const CoreState &core, unsigned begin,
                            unsigned end) const;
    double remoteProbability(const CoreState &core, unsigned ways) const;
    double queueWaitCycles(double lambda) const;

  public:
    PARAMS(UACCController);
    UACCController(const Params &p);

    void startup() override;

    bool remoteEnabled(unsigned coreId, PacketPtr pkt) const;
    unsigned allocation(unsigned coreId) const;

    void recordLocalMiss(unsigned coreId, Addr addr);
    void recordLocalCacheHit(unsigned coreId);
    void recordRemoteLookup(unsigned coreId);
    void recordRemoteAccess(unsigned coreId, bool hit);
    void recordAtomicSwapProbe(unsigned coreId);
    void recordAtomicSwapHit(unsigned coreId);
    void recordAtomicSwap(unsigned coreId);
};

} // namespace gem5

#endif // __MEM_UACC_UACC_CONTROLLER_HH__
