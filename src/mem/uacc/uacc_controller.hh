#ifndef __MEM_UACC_UACC_CONTROLLER_HH__
#define __MEM_UACC_UACC_CONTROLLER_HH__

#include <cstdint>
#include <array>
#include <list>
#include <map>
#include <string>
#include <vector>

#include "base/statistics.hh"
#include "base/types.hh"
#include "mem/packet.hh"
#include "mem/uacc/uacc_extension.hh"
#include "params/UACCController.hh"
#include "sim/clocked_object.hh"
#include "sim/eventq.hh"

namespace gem5
{

class UACCPartitionManager;

class UACCController : public ClockedObject
{
  private:
    enum class QueueDirection : unsigned
    {
        Request = 0,
        Response = 1,
    };

    static constexpr unsigned queueClassCount = static_cast<unsigned>(
        UACCRequestExtension::QueueClass::Count);

    struct QueueWindow
    {
        uint64_t packetCount = 0;
        std::vector<Tick> arrivalTicks;
        uint64_t interArrivalSamples = 0;
        long double interArrivalSum = 0.0;
        long double interArrivalSquareSum = 0.0;
        std::array<uint64_t, queueClassCount> classCount{};
        std::array<long double, queueClassCount> classServiceSum{};
        std::array<long double, queueClassCount> classServiceSquareSum{};
        long double serviceSum = 0.0;
        long double serviceSquareSum = 0.0;
        long double observedWaitSum = 0.0;
        uint64_t observedWaitSamples = 0;
        uint64_t queueOccupancySum = 0;
        uint64_t queueOccupancyMax = 0;
        uint64_t bufferFullEvents = 0;
        uint64_t backpressureEvents = 0;
    };

    struct QueueState
    {
        uint64_t packetCount = 0;
        Tick lastArrival = 0;
        bool haveLastArrival = false;
        uint64_t interArrivalSamples = 0;
        long double interArrivalSum = 0.0;
        long double interArrivalSquareSum = 0.0;
        long double serviceSum = 0.0;
        long double serviceSquareSum = 0.0;
        long double observedWaitSum = 0.0;
        uint64_t observedWaitSamples = 0;
        uint64_t queueOccupancySum = 0;
        uint64_t queueOccupancyMax = 0;
        uint64_t bufferFullEvents = 0;
        uint64_t backpressureEvents = 0;
        std::array<uint64_t, queueClassCount> classCount{};
        std::array<long double, queueClassCount> classServiceSum{};
        std::array<long double, queueClassCount> classServiceSquareSum{};
        std::map<uint64_t, QueueWindow> pendingWindows;
        uint64_t processedWindow = 0;
        bool haveProcessedWindow = false;
        unsigned congestionWindows = 0;
        double ca2 = 1.0;
        double ca2Ewma = 1.0;
        double serviceMean = 0.0;
        double serviceSecondMoment = 0.0;
        double cs2 = 0.0;
        double utilization = 0.0;
        double observedWait = 0.0;
        double feedbackBeta = 1.0;
    };

    struct CoreState
    {
        std::vector<std::list<Addr>> atd;
        std::vector<uint64_t> reuseHistogram;
        uint64_t localMisses = 0;
        uint64_t remoteLookups = 0;
        uint64_t remoteHits = 0;
        std::array<QueueState, 2> queues;
    };

    struct TrafficSums
    {
        double count = 0.0;
        double busy = 0.0;
        double busySquare = 0.0;
    };

    struct QueueCost
    {
        double cost = 0.0;
        double wait = 0.0;
        double utilization = 0.0;
        bool valid = true;
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
        statistics::Vector queuePackets;
        statistics::Vector queueCA2;
        statistics::Vector queueCS2;
        statistics::Vector queueUtilization;
        statistics::Vector queueObservedWait;
        statistics::Vector queuePredictedWait;
        statistics::Vector queueFeedback;
        statistics::Vector queueBackpressure;
        statistics::Vector queueOccupancy;
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
    const std::string queueModel;
    const unsigned minArrivalSamples;
    const unsigned ca2EwmaShift;
    const unsigned feedbackEwmaShift;
    const double feedbackBetaMax;
    const double rhoMax;
    const unsigned queueOccupancyThreshold;
    const unsigned backpressureThreshold;
    const unsigned contractionWindows;
    const unsigned requestSize;

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
    double queueWaitCycles(double lambda) const;
    QueueState &queueState(unsigned coreId, QueueDirection direction);
    const QueueState &queueState(unsigned coreId,
                                 QueueDirection direction) const;
    void updateQueueState(QueueState &queue, QueueWindow &window);
    void updateFeedback(QueueState &queue);
    TrafficSums trafficFor(unsigned coreId, unsigned ways,
                           QueueDirection direction) const;
    TrafficSums observedTraffic(const QueueState &queue) const;
    QueueCost modelQueueCost(const QueueState &queue,
                             const TrafficSums &traffic,
                             bool useFeedback) const;
    QueueCost legacyQueueCost(double packetCount) const;
    double fixedCost(const std::vector<unsigned> &allocation) const;
    double totalQueueCost(const std::vector<unsigned> &allocation,
                          bool useFeedback,
                          bool *valid = nullptr) const;
    double capacityGain(const std::vector<unsigned> &allocation) const;
    uint64_t queueWindowIndex(Tick tick) const;
    bool queueCongested(const QueueState &queue) const;
    void recordQueuePacket(unsigned coreId, QueueDirection direction,
                           const UACCRequestExtension::QueueTiming &timing);

  public:
    PARAMS(UACCController);
    UACCController(const Params &p);

    void startup() override;

    bool remoteEnabled(unsigned coreId, PacketPtr pkt) const;
    unsigned allocation(unsigned coreId) const;

    void recordLocalMiss(unsigned coreId, Addr addr);
    void recordLocalCacheHit(unsigned coreId);
    void recordRemoteLookup(unsigned coreId);
    void recordRemoteAccess(unsigned coreId, bool hit,
                            PacketPtr pkt = nullptr);
    void recordRemoteResponse(unsigned coreId, PacketPtr pkt);
    void recordRemoteBackpressure(unsigned coreId);
    void recordRemoteResponseBackpressure(unsigned coreId);
    void recordAtomicSwapProbe(unsigned coreId);
    void recordAtomicSwapHit(unsigned coreId);
    void recordAtomicSwap(unsigned coreId);
};

} // namespace gem5

#endif // __MEM_UACC_UACC_CONTROLLER_HH__
