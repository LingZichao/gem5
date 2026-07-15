#include "mem/uacc/uacc_controller.hh"

#include <algorithm>
#include <cmath>
#include <limits>
#include <numeric>

#include "base/logging.hh"
#include "debug/UACC.hh"
#include "mem/uacc/uacc_partition.hh"
#include "mem/uacc/uacc_queue_model.hh"
#include "sim/core.hh"

namespace gem5
{

namespace
{

constexpr double Ca2Max = 1.0e6;

} // anonymous namespace

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
               "Last selected marginal utility per core"),
      ADD_STAT(queuePackets, statistics::units::Count::get(),
               "Packets observed at each UACC request/response queue"),
      ADD_STAT(queueCA2, statistics::units::Ratio::get(),
               "Effective arrival-interval squared coefficient of variation"),
      ADD_STAT(queueCS2, statistics::units::Ratio::get(),
               "Service-time squared coefficient of variation"),
      ADD_STAT(queueUtilization, statistics::units::Ratio::get(),
               "Observed queue utilization"),
      ADD_STAT(queueObservedWait, statistics::units::Cycle::get(),
               "Observed mean queue wait"),
      ADD_STAT(queuePredictedWait, statistics::units::Cycle::get(),
               "Predicted mean queue wait"),
      ADD_STAT(queueFeedback, statistics::units::Ratio::get(),
               "Measured queue feedback multiplier"),
      ADD_STAT(queueBackpressure, statistics::units::Count::get(),
               "Observed queue backpressure events"),
      ADD_STAT(queueOccupancy, statistics::units::Count::get(),
               "Maximum observed queue occupancy")
{
    allocationWays.init(controller.numCores);
    lastUtility.init(controller.numCores);
    queuePackets.init(controller.numCores * 2);
    queueCA2.init(controller.numCores * 2);
    queueCS2.init(controller.numCores * 2);
    queueUtilization.init(controller.numCores * 2);
    queueObservedWait.init(controller.numCores * 2);
    queuePredictedWait.init(controller.numCores * 2);
    queueFeedback.init(controller.numCores * 2);
    queueBackpressure.init(controller.numCores * 2);
    queueOccupancy.init(controller.numCores * 2);
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
      queueModel(p.queue_model),
      minArrivalSamples(p.min_arrival_samples),
      ca2EwmaShift(p.ca2_ewma_shift),
      feedbackEwmaShift(p.feedback_ewma_shift),
      feedbackBetaMax(std::max(1.0, p.feedback_beta_max)),
      rhoMax(std::min(1.0, std::max(0.0, p.rho_max))),
      queueOccupancyThreshold(p.queue_occupancy_threshold),
      backpressureThreshold(p.backpressure_threshold),
      contractionWindows(std::max(1u, p.contraction_windows)),
      recoveryWindows(std::max(1u, p.recovery_windows)),
      requestSize(std::max(1u, p.request_size)),
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
    fatal_if(queueModel != "mg1" && queueModel != "gg1" &&
             queueModel != "gg1-feedback" && queueModel != "section14" &&
             queueModel != "section14-feedback",
             "Unknown UACC queue model '%s'", queueModel);
    fatal_if(rhoMax <= 0.0 || rhoMax >= 1.0,
             "UACC rho_max must be in (0, 1)");

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
UACCController::recordRemoteAccess(unsigned coreId, bool hit, PacketPtr pkt)
{
    if (coreId >= cores.size())
        return;

    if (hit) {
        ++cores[coreId].remoteHits;
        ++stats.remoteHits;
    } else {
        ++stats.remoteMisses;
    }

    if (pkt && pkt->req) {
        if (auto ext = pkt->req->getExtension<UACCRequestExtension>()) {
            recordQueuePacket(coreId, QueueDirection::Request,
                              ext->request_queue);
            ext->request_queue.valid = false;
        }
    }
}

void
UACCController::recordRemoteResponse(unsigned coreId, PacketPtr pkt)
{
    if (coreId >= cores.size() || !pkt || !pkt->req)
        return;

    if (auto ext = pkt->req->getExtension<UACCRequestExtension>()) {
        recordQueuePacket(coreId, QueueDirection::Response,
                          ext->response_queue);
        ext->response_queue.valid = false;
    }
}

void
UACCController::recordRemoteBackpressure(unsigned coreId)
{
    if (coreId >= cores.size())
        return;
    auto &queue = queueState(coreId, QueueDirection::Request);
    ++queue.pendingWindows[queueWindowIndex(curTick())].backpressureEvents;
}

void
UACCController::recordRemoteResponseBackpressure(unsigned coreId)
{
    if (coreId >= cores.size())
        return;
    auto &queue = queueState(coreId, QueueDirection::Response);
    ++queue.pendingWindows[queueWindowIndex(curTick())].backpressureEvents;
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

UACCController::QueueState &
UACCController::queueState(unsigned coreId, QueueDirection direction)
{
    return cores[coreId].queues[static_cast<unsigned>(direction)];
}

const UACCController::QueueState &
UACCController::queueState(unsigned coreId, QueueDirection direction) const
{
    return cores[coreId].queues[static_cast<unsigned>(direction)];
}

uint64_t
UACCController::queueWindowIndex(Tick tick) const
{
    const Tick windowTicks = static_cast<Tick>(profileInterval) *
        clockPeriod();
    return windowTicks == 0 ? 0 : tick / windowTicks;
}

void
UACCController::recordQueuePacket(
    unsigned coreId, QueueDirection direction,
    const UACCRequestExtension::QueueTiming &timing)
{
    if (coreId >= cores.size() || !timing.valid)
        return;

    auto &queue = queueState(coreId, direction);
    uint64_t windowIndex = queueWindowIndex(timing.enqueue);
    // A packet may complete after its enqueue window has already crossed the
    // grace period.  Attribute that late sample to the next open window so it
    // cannot recreate an already processed bucket indefinitely.
    if (queue.haveProcessedWindow && windowIndex <= queue.processedWindow)
        windowIndex = queue.processedWindow + 1;
    auto &window = queue.pendingWindows[windowIndex];
    ++window.packetCount;
    window.arrivalTicks.push_back(timing.enqueue);

    const double period = std::max(1.0,
        static_cast<double>(clockPeriod()));
    const double service = timing.service_ticks / period;
    window.serviceSum += service;
    window.serviceSquareSum += service * service;
    const unsigned classId = std::min(
        static_cast<unsigned>(timing.queue_class), queueClassCount - 1);
    ++window.classCount[classId];
    window.classServiceSum[classId] += service;
    window.classServiceSquareSum[classId] += service * service;

    // service_start is the actual start of serialization.  SerialLink has
    // already removed the packet's busy time when it records this field; do
    // not remove it a second time here.  Fixed link delay is excluded from
    // Wq, and unsigned arithmetic is clipped at both boundaries.
    const Tick waitTicks = uacc::observedQueueWait(
        timing.enqueue, timing.service_start, timing.fixed_ticks);
    window.observedWaitSum += waitTicks / period;
    ++window.observedWaitSamples;

    window.queueOccupancySum += timing.occupancy;
    window.queueOccupancyMax = std::max<uint64_t>(
        window.queueOccupancyMax, timing.occupancy);
    window.bufferFullEvents += timing.buffer_full_events;
}

void
UACCController::updateQueueState(QueueState &queue, QueueWindow &window)
{
    std::sort(window.arrivalTicks.begin(), window.arrivalTicks.end());
    for (const Tick arrival : window.arrivalTicks) {
        if (queue.haveLastArrival && arrival >= queue.lastArrival) {
            const long double interval =
                static_cast<long double>(arrival - queue.lastArrival);
            ++window.interArrivalSamples;
            window.interArrivalSum += interval;
            window.interArrivalSquareSum += interval * interval;
        }
        if (!queue.haveLastArrival || arrival > queue.lastArrival) {
            queue.lastArrival = arrival;
            queue.haveLastArrival = true;
        }
    }

    queue.interArrivalSamples = window.interArrivalSamples;
    queue.interArrivalSum = window.interArrivalSum;
    queue.interArrivalSquareSum = window.interArrivalSquareSum;
    if (window.interArrivalSamples >= minArrivalSamples) {
        queue.ca2 = uacc::arrivalCa2(
            window.interArrivalSamples, window.interArrivalSum,
            window.interArrivalSquareSum, queue.ca2, Ca2Max);
        const double shift = std::ldexp(1.0,
            std::min<unsigned>(ca2EwmaShift, 30));
        queue.ca2Ewma += (queue.ca2 - queue.ca2Ewma) / shift;
        queue.ca2Ewma = std::max(0.0, queue.ca2Ewma);
    }

    queue.packetCount = window.packetCount;
    queue.serviceSum = window.serviceSum;
    queue.serviceSquareSum = window.serviceSquareSum;
    queue.observedWaitSum = window.observedWaitSum;
    queue.observedWaitSamples = window.observedWaitSamples;
    queue.queueOccupancySum = window.queueOccupancySum;
    queue.queueOccupancyMax = window.queueOccupancyMax;
    queue.bufferFullEvents = window.bufferFullEvents;
    queue.backpressureEvents = window.backpressureEvents;
    queue.classCount = window.classCount;
    queue.classServiceSum = window.classServiceSum;
    queue.classServiceSquareSum = window.classServiceSquareSum;

    if (queue.packetCount != 0) {
        queue.serviceMean = static_cast<double>(
            queue.serviceSum / queue.packetCount);
        queue.serviceSecondMoment = static_cast<double>(
            queue.serviceSquareSum / queue.packetCount);
        if (queue.serviceMean > 0.0) {
            queue.cs2 = std::max(0.0, queue.serviceSecondMoment /
                (queue.serviceMean * queue.serviceMean) - 1.0);
        }
    }
    queue.utilization = static_cast<double>(queue.serviceSum) /
        static_cast<double>(profileInterval);

    queue.observedWait = queue.observedWaitSamples == 0 ? 0.0 :
        static_cast<double>(queue.observedWaitSum /
                            queue.observedWaitSamples);
}

void
UACCController::updateFeedback(QueueState &queue)
{
    if (queue.packetCount < minArrivalSamples)
        return;

    const QueueCost model = modelQueueCost(queue, observedTraffic(queue),
                                           false);
    const double observedCost = static_cast<double>(queue.observedWaitSum);
    double raw = 1.0;
    if (!std::isfinite(model.cost) || model.cost <= 1e-12)
        raw = observedCost > 1e-12 ? feedbackBetaMax : 1.0;
    else
        raw = std::clamp(observedCost / model.cost, 1.0,
                         feedbackBetaMax);

    const double shift = std::ldexp(1.0,
        std::min<unsigned>(feedbackEwmaShift, 30));
    queue.feedbackBeta += (raw - queue.feedbackBeta) / shift;
    queue.feedbackBeta = std::clamp(queue.feedbackBeta, 1.0,
                                    feedbackBetaMax);
}

UACCController::TrafficSums
UACCController::observedTraffic(const QueueState &queue) const
{
    return TrafficSums{
        static_cast<double>(queue.packetCount),
        static_cast<double>(queue.serviceSum),
        static_cast<double>(queue.serviceSquareSum)};
}

UACCController::TrafficSums
UACCController::trafficFor(unsigned coreId, unsigned ways,
                            QueueDirection direction) const
{
    if (coreId >= cores.size() || ways == 0)
        return {};

    const auto &core = cores[coreId];
    const double misses = static_cast<double>(core.localMisses);
    const double hitCount = std::min(
        misses, static_cast<double>(histogramCount(
            core, baseWays, baseWays + ways)));
    const auto &queue = queueState(coreId, direction);
    const double ticksPerCycle = d2dBandwidth /
        std::max(1.0, static_cast<double>(clockPeriod()));
    const double requestFallback = std::max(1.0,
        static_cast<double>(requestSize) * ticksPerCycle);
    const double responseFallback = std::max(1.0,
        static_cast<double>(cacheLineSize) * ticksPerCycle);

    auto classMean = [&](UACCRequestExtension::QueueClass cls,
                         double fallback) {
        const unsigned id = static_cast<unsigned>(cls);
        if (queue.classCount[id] == 0)
            return fallback;
        const double measured = static_cast<double>(
            queue.classServiceSum[id] / queue.classCount[id]);
        return measured > 0.0 ? measured : fallback;
    };
    auto classSecond = [&](UACCRequestExtension::QueueClass cls,
                           double fallback) {
        const unsigned id = static_cast<unsigned>(cls);
        if (queue.classCount[id] == 0)
            return fallback * fallback;
        const double measured = static_cast<double>(
            queue.classServiceSquareSum[id] / queue.classCount[id]);
        return measured > 0.0 ? measured : fallback * fallback;
    };
    TrafficSums result;
    auto add = [&](double count, double service, double second) {
        result.count += count;
        result.busy += count * service;
        result.busySquare += count * second;
    };

    if (direction == QueueDirection::Request) {
        add(misses, classMean(UACCRequestExtension::QueueClass::Request,
                              requestFallback),
            classSecond(UACCRequestExtension::QueueClass::Request,
                        requestFallback));
        const unsigned wb = static_cast<unsigned>(
            UACCRequestExtension::QueueClass::Writeback);
        add(queue.classCount[wb],
            classMean(UACCRequestExtension::QueueClass::Writeback,
                      responseFallback),
            classSecond(UACCRequestExtension::QueueClass::Writeback,
                        responseFallback));
    } else {
        add(hitCount,
            classMean(UACCRequestExtension::QueueClass::HitResponse,
                      responseFallback),
            classSecond(UACCRequestExtension::QueueClass::HitResponse,
                        responseFallback));
        add(misses - hitCount,
            classMean(UACCRequestExtension::QueueClass::MissResponse,
                      responseFallback),
            classSecond(UACCRequestExtension::QueueClass::MissResponse,
                        responseFallback));
    }
    return result;
}

UACCController::QueueCost
UACCController::modelQueueCost(const QueueState &queue,
                               const TrafficSums &traffic,
                               bool useFeedback) const
{
    if (traffic.count <= 0.0 || traffic.busy <= 0.0)
        return {};

    const double window = static_cast<double>(profileInterval);
    const double utilization = traffic.busy / window;
    const double slack = window - traffic.busy;
    QueueCost result;
    result.utilization = utilization;
    result.valid = utilization < rhoMax;
    if (!result.valid || slack <= 0.0)
        return {std::numeric_limits<double>::infinity(),
                std::numeric_limits<double>::infinity(), utilization, false};

    const double ca2 = std::max(1.0, queue.ca2Ewma);
    if (queueModel == "gg1" || queueModel == "gg1-feedback") {
        const double serviceMean = traffic.busy / traffic.count;
        const double cs2 = serviceMean > 0.0 ? std::max(0.0,
            traffic.busySquare / traffic.count /
            (serviceMean * serviceMean) - 1.0) : 0.0;
        result.wait = utilization / (1.0 - utilization) *
            (ca2 + cs2) / 2.0 * serviceMean;
        result.cost = traffic.count * result.wait;
    } else {
        // Section 14: direct window-count implementation.  This is kept as
        // a separate path so the engineering datapath is exercised directly,
        // even though it is algebraically equivalent to the moment form.
        result.cost = uacc::section14QueueCost(
            traffic.count, traffic.busy, traffic.busySquare, ca2, window);
        result.wait = result.cost / traffic.count;
    }

    if (useFeedback)
        result.cost *= queue.feedbackBeta;
    result.wait = result.cost / traffic.count;
    return result;
}

UACCController::QueueCost
UACCController::legacyQueueCost(double packetCount) const
{
    if (packetCount <= 0.0)
        return {};
    const double intervalSeconds = static_cast<double>(profileInterval) *
        clockPeriod() / sim_clock::as_float::s;
    const double lambda = packetCount * responseFlits /
        std::max(intervalSeconds, 1e-30);
    const double wait = queueWaitCycles(lambda);
    return {packetCount * wait, wait, 0.0, std::isfinite(wait)};
}

double
UACCController::fixedCost(const std::vector<unsigned> &allocation) const
{
    if (queueModel == "mg1")
        return 0.0;

    double packets = 0.0;
    for (unsigned coreId = 0; coreId < numCores; ++coreId) {
        if (coreId >= allocation.size() || allocation[coreId] == 0)
            continue;
        const double missCount = cores[coreId].localMisses;
        const double writebacks = cores[coreId].queues[
            static_cast<unsigned>(QueueDirection::Request)].classCount[
            static_cast<unsigned>(UACCRequestExtension::QueueClass::Writeback)];
        packets += 2.0 * missCount + writebacks;
    }
    const double oneWayFixed = static_cast<double>(d2dRtt) /
        std::max(1.0, static_cast<double>(clockPeriod())) / 2.0;
    return packets * oneWayFixed;
}

bool
UACCController::queueCongested(const QueueState &queue) const
{
    return queue.utilization >= rhoMax ||
        (queueOccupancyThreshold != 0 &&
         queue.queueOccupancyMax > queueOccupancyThreshold) ||
        (backpressureThreshold != 0 &&
         (queue.backpressureEvents + queue.bufferFullEvents) >
             backpressureThreshold);
}

double
UACCController::totalQueueCost(const std::vector<unsigned> &allocation,
                                bool useFeedback, bool *valid) const
{
    bool allValid = true;
    double total = 0.0;
    if (queueModel == "mg1") {
        double packetCount = 0.0;
        for (unsigned coreId = 0; coreId < numCores; ++coreId) {
            if (coreId < allocation.size() && allocation[coreId] != 0)
                packetCount += cores[coreId].localMisses;
        }
        const QueueCost cost = legacyQueueCost(packetCount);
        if (!cost.valid)
            allValid = false;
        total = cost.cost;
    } else {
        for (unsigned coreId = 0; coreId < numCores; ++coreId) {
            for (const auto direction : {QueueDirection::Request,
                                         QueueDirection::Response}) {
                const auto &queue = queueState(coreId, direction);
                const QueueCost cost = modelQueueCost(
                    queue, trafficFor(coreId,
                        coreId < allocation.size() ? allocation[coreId] : 0,
                        direction), useFeedback);
                if (queueOccupancyThreshold != 0 &&
                    queue.queueOccupancyMax > queueOccupancyThreshold)
                    allValid = false;
                if (backpressureThreshold != 0 &&
                    queue.backpressureEvents + queue.bufferFullEvents >
                        backpressureThreshold)
                    allValid = false;
                if (!cost.valid)
                    allValid = false;
                total += cost.cost;
            }
        }
    }
    if (valid)
        *valid = allValid;
    return total;
}

double
UACCController::capacityGain(const std::vector<unsigned> &allocation) const
{
    double gain = 0.0;
    for (unsigned coreId = 0; coreId < numCores; ++coreId) {
        if (coreId >= allocation.size())
            continue;
        const auto &core = cores[coreId];
        const unsigned ways = allocation[coreId];
        const double hits = histogramCount(core, baseWays,
                                           baseWays + ways);
        const double distance = coreId < distanceNs.size() ?
            distanceNs[coreId] : 0.0;
        const double discount = allocationPolicy == "greedy" ? 1.0 :
            std::max(0.0, 1.0 - distance * distanceDiscountPerNs);
        gain += hits * lowerMissPenalty * discount;
    }
    return gain;
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

    const uint64_t currentWindow = queueWindowIndex(curTick());
    // Give deferred cache/selector completion callbacks one full profiling
    // window to arrive.  Samples are still assigned by enqueue window, so a
    // response crossing a boundary cannot affect the newer window.
    bool contract = false;
    if (currentWindow >= 2) {
        const uint64_t lastCompleteWindow = currentWindow - 2;
        for (auto &core : cores) {
            for (auto &queue : core.queues) {
                uint64_t windowIndex = queue.haveProcessedWindow ?
                    queue.processedWindow + 1 : 0;
                while (windowIndex <= lastCompleteWindow) {
                    QueueWindow emptyWindow;
                    auto it = queue.pendingWindows.find(windowIndex);
                    QueueWindow &window = it == queue.pendingWindows.end() ?
                        emptyWindow : it->second;
                    updateQueueState(queue, window);
                    queue.haveProcessedWindow = true;
                    queue.processedWindow = windowIndex;
                    if (queueModel != "mg1")
                        updateFeedback(queue);
                    if (queueCongested(queue))
                        ++queue.congestionWindows;
                    else
                        queue.congestionWindows = 0;
                    contract |=
                        queue.congestionWindows >= contractionWindows;
                    if (it != queue.pendingWindows.end())
                        queue.pendingWindows.erase(it);
                    ++windowIndex;
                }
            }
        }
    }

    std::vector<unsigned> next = allocations;
    std::vector<double> utilities(numCores, 0.0);
    unsigned allocated = std::accumulate(next.begin(), next.end(), 0u);
    const bool useFeedback = queueModel == "gg1-feedback" ||
        queueModel == "section14" || queueModel == "section14-feedback";

    if (contract) {
        recoveryCooldown = recoveryWindows;
        if (allocated != 0) {
            unsigned contractCore = numCores;
            double lowestBenefit = std::numeric_limits<double>::infinity();
            for (unsigned coreId = 0; coreId < numCores; ++coreId) {
                if (next[coreId] == 0)
                    continue;
                std::vector<unsigned> smaller = next;
                --smaller[coreId];
                const double benefit = capacityGain(next) -
                    capacityGain(smaller);
                if (benefit < lowestBenefit) {
                    lowestBenefit = benefit;
                    contractCore = coreId;
                }
            }
            if (contractCore != numCores) {
                --next[contractCore];
                --allocated;
                DPRINTF(UACC, "allocator model=%s contracted core %u to %u "
                        "after %u congested windows\n", queueModel,
                        contractCore, next[contractCore], contractionWindows);
            }
        }
    } else if (recoveryCooldown != 0) {
        --recoveryCooldown;
    } else if (allocated < maxRemoteWays) {
        double bestScore = 0.0;
        double bestUtility = 0.0;
        unsigned bestCore = numCores;
        unsigned bestDelta = 0;

        for (unsigned coreId = 0; coreId < numCores; ++coreId) {
            const auto &state = cores[coreId];
            if (state.localMisses == 0)
                continue;

            for (const unsigned delta : lookaheadDeltas) {
                if (delta == 0 || allocated + delta > maxRemoteWays ||
                    next[coreId] + delta > maxRemoteWays)
                    continue;

                std::vector<unsigned> candidate = next;
                candidate[coreId] += delta;

                bool currentValid = true;
                bool candidateValid = true;
                const double currentQueue = totalQueueCost(
                    next, useFeedback, &currentValid);
                const double candidateQueue = totalQueueCost(
                    candidate, useFeedback, &candidateValid);
                const double currentFixed = fixedCost(next);
                const double candidateFixed = fixedCost(candidate);
                const double gain = capacityGain(candidate) -
                    capacityGain(next);
                const bool congestionEnabled = allocationPolicy ==
                    "congestion";
                const double queueDelta = congestionEnabled ?
                    candidateQueue - currentQueue : 0.0;
                const double fixedDelta = congestionEnabled ?
                    candidateFixed - currentFixed : 0.0;
                const double utility = gain - fixedDelta - queueDelta;
                const double score = utility / delta;

                if (currentValid && candidateValid &&
                    std::isfinite(score) && score > bestScore) {
                    bestScore = score;
                    bestUtility = utility;
                    bestCore = coreId;
                    bestDelta = delta;
                }
            }
        }

        if (bestCore != numCores) {
            ++next[bestCore];
            ++allocated;
            utilities[bestCore] = bestUtility;

            DPRINTF(UACC, "allocator model=%s selected core %u utility=%f "
                    "score=%f lookahead=%u ways=%u\n", queueModel,
                    bestCore, bestUtility, bestScore, bestDelta,
                    next[bestCore]);
        }
    }

    for (unsigned coreId = 0; coreId < numCores; ++coreId) {
        for (unsigned direction = 0; direction < 2; ++direction) {
            auto &queue = cores[coreId].queues[direction];
            const auto candidateTraffic = trafficFor(
                coreId, next[coreId], static_cast<QueueDirection>(direction));
            QueueCost predicted;
            if (queueModel == "mg1") {
                double remotePackets = 0.0;
                for (unsigned candidateCore = 0;
                     candidateCore < numCores; ++candidateCore) {
                    if (next[candidateCore] != 0)
                        remotePackets += cores[candidateCore].localMisses;
                }
                predicted = legacyQueueCost(remotePackets);
            } else {
                predicted = modelQueueCost(queue, candidateTraffic,
                                           useFeedback);
            }
            const unsigned index = coreId * 2 + direction;
            stats.queuePackets[index] = queue.packetCount;
            stats.queueCA2[index] = std::max(1.0, queue.ca2Ewma);
            stats.queueCS2[index] = queue.cs2;
            stats.queueUtilization[index] = queue.utilization;
            stats.queueObservedWait[index] = queue.observedWait;
            stats.queuePredictedWait[index] = predicted.wait;
            stats.queueFeedback[index] = queue.feedbackBeta;
            stats.queueBackpressure[index] = queue.backpressureEvents +
                queue.bufferFullEvents;
            stats.queueOccupancy[index] = queue.queueOccupancyMax;
        }
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
