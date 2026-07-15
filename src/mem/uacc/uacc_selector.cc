#include "mem/uacc/uacc_selector.hh"

#include <algorithm>
#include <cstring>

#include "base/logging.hh"
#include "debug/UACC.hh"
#include "mem/uacc/uacc_controller.hh"
#include "mem/uacc/uacc_extension.hh"
#include "params/UACCSelector.hh"

namespace gem5
{

UACCSelector::CpuSidePort::CpuSidePort(const std::string &name,
                                       UACCSelector &parent)
    : ResponsePort(name), selector(parent)
{
}

UACCSelector::DownstreamPort::DownstreamPort(const std::string &name,
                                             UACCSelector &parent,
                                             bool is_remote)
    : RequestPort(name), selector(parent), remote(is_remote)
{
}

UACCSelector::UACCSelector(const Params &p)
    : ClockedObject(p),
      cpuSidePort(name() + ".cpu_side_port", *this),
      remoteSidePort(name() + ".remote_side_port", *this, true),
      memSidePort(name() + ".mem_side_port", *this, false),
      controller(p.controller),
      coreId(p.core_id),
      cacheLineSize(std::max(1u, p.cache_line_size)),
      localSets(std::max(1u, static_cast<unsigned>(
          p.local_size /
          (static_cast<uint64_t>(std::max(1u, p.cache_line_size)) *
           std::max(1u, p.local_assoc))))),
      localAssoc(std::max(1u, p.local_assoc)),
      atomicSwap(p.atomic_swap),
      writebackEvent([this]{ tryWriteback(); }, name() + ".writeback")
{
    fatal_if(p.local_assoc == 0,
             "UACCSelector local_assoc must be non-zero");
    fatal_if(p.local_size <
                 static_cast<uint64_t>(cacheLineSize) * localAssoc,
             "UACCSelector local_size must hold at least one line");
}

void
UACCSelector::init()
{
    fatal_if(!cpuSidePort.isConnected() || !remoteSidePort.isConnected() ||
             !memSidePort.isConnected(),
             "UACCSelector requires all three ports to be connected");
    cpuSidePort.sendRangeChange();
}

Port &
UACCSelector::getPort(const std::string &if_name, PortID idx)
{
    if (if_name == "cpu_side_port")
        return cpuSidePort;
    if (if_name == "remote_side_port")
        return remoteSidePort;
    if (if_name == "mem_side_port")
        return memSidePort;
    return ClockedObject::getPort(if_name, idx);
}

bool
UACCSelector::useRemote(PacketPtr pkt) const
{
    return controller && controller->remoteEnabled(coreId, pkt);
}

std::shared_ptr<UACCRequestExtension>
UACCSelector::annotate(PacketPtr pkt, bool swapProbe) const
{
    if (!pkt || !pkt->req)
        return nullptr;

    auto extension = pkt->req->getExtension<UACCRequestExtension>();
    if (!extension)
        extension = std::make_shared<UACCRequestExtension>(coreId);
    extension->core_id = coreId;
    extension->atomic_swap_probe = swapProbe;
    pkt->req->setExtension(extension);
    return extension;
}

RequestPort &
UACCSelector::downstream(bool remote)
{
    return remote ? static_cast<RequestPort &>(remoteSidePort) :
                    static_cast<RequestPort &>(memSidePort);
}

const RequestPort &
UACCSelector::downstream(bool remote) const
{
    return remote ? static_cast<const RequestPort &>(remoteSidePort) :
                    static_cast<const RequestPort &>(memSidePort);
}

void
UACCSelector::recordForwarded(Addr addr, bool trackMiss, bool remote)
{
    DPRINTF(UACC, "selector core=%u forwarded addr=%#x track_miss=%d "
            "remote=%d\n", coreId, addr, trackMiss, remote);
    if (!controller)
        return;
    if (trackMiss)
        controller->recordLocalMiss(coreId, addr);
    if (remote)
        controller->recordRemoteLookup(coreId);
}

bool
UACCSelector::localCacheRequest(PacketPtr pkt) const
{
    return atomicSwap && pkt && pkt->req &&
        !pkt->req->isUncacheable() && !pkt->isEviction() &&
        !pkt->req->isSwap() && !pkt->req->isAtomic() &&
        !pkt->req->isLLSC() && !pkt->req->isLockedRMW() &&
        (pkt->isRead() || pkt->isWrite());
}

unsigned
UACCSelector::setIndex(Addr addr) const
{
    return static_cast<unsigned>((addr / cacheLineSize) % localSets);
}

UACCSelector::LineKey
UACCSelector::lineKey(PacketPtr pkt) const
{
    return lineKey(pkt->getBlockAddr(cacheLineSize), pkt->isSecure());
}

UACCSelector::LineKey
UACCSelector::lineKey(Addr addr, bool secure) const
{
    return {addr, secure};
}

UACCSelector::LocalLine *
UACCSelector::findLocalLine(const LineKey &key)
{
    auto it = localLines.find(key);
    return it == localLines.end() ? nullptr : &it->second;
}

UACCSelector::LocalLine *
UACCSelector::findVictim(unsigned set)
{
    LocalLine *victim = nullptr;
    for (auto &entry : localLines) {
        if (setIndex(entry.first.addr) != set)
            continue;
        if (!victim || entry.second.lastUse < victim->lastUse)
            victim = &entry.second;
    }
    return victim;
}

void
UACCSelector::touch(LocalLine &line)
{
    line.lastUse = ++lruSequence;
}

void
UACCSelector::prepareVictim(
    const std::shared_ptr<UACCRequestExtension> &ext, unsigned set)
{
    ext->victim_valid = false;
    ext->victim_data.clear();

    LocalLine *victim = findVictim(set);
    if (!victim)
        return;

    for (const auto &entry : localLines) {
        if (&entry.second != victim)
            continue;

        ext->victim_valid = true;
        ext->victim_addr = entry.first.addr;
        ext->victim_secure = entry.first.secure;
        ext->victim_dirty = victim->dirty;
        ext->victim_writable = victim->writable;
        ext->victim_data = victim->data;
        return;
    }
}

PacketPtr
UACCSelector::makeProbe(PacketPtr original, bool remote,
                        std::shared_ptr<UACCRequestExtension> &ext)
{
    RequestPtr probe_req = std::make_shared<Request>(*original->req);
    probe_req->setPaddr(original->getBlockAddr(cacheLineSize));
    PacketPtr probe = new Packet(probe_req, MemCmd::ReadReq, cacheLineSize);
    probe->allocate();

    ext = annotate(probe, remote && atomicSwap);
    if (ext && ext->atomic_swap_probe) {
        prepareVictim(ext, setIndex(probe->getAddr()));
        if (controller)
            controller->recordAtomicSwapProbe(coreId);
    }
    return probe;
}

bool
UACCSelector::sendProbe(PacketPtr original, bool remote)
{
    const LineKey key = lineKey(original);
    const unsigned set = setIndex(key.addr);
    if (busySets.count(set))
        return false;

    std::shared_ptr<UACCRequestExtension> ext;
    PacketPtr probe = makeProbe(original, remote, ext);
    pendingProbes.emplace(probe, PendingProbe{original, probe, set, remote});
    busySets.insert(set);

    if (!downstream(remote).sendTimingReq(probe)) {
        cpuRetryPending = true;
        if (remote && controller)
            controller->recordRemoteBackpressure(coreId);
        if (!remote)
            memRetryKind = MemRetryKind::CpuRequest;
        pendingProbes.erase(probe);
        busySets.erase(set);
        delete probe;
        return false;
    }

    if (controller) {
        controller->recordLocalMiss(coreId, key.addr);
        if (remote)
            controller->recordRemoteLookup(coreId);
    }
    return true;
}

bool
UACCSelector::serveLocal(PacketPtr pkt, LocalLine &line)
{
    if (pkt->isRead()) {
        if (!pkt->hasDataPtr())
            pkt->allocate();
        pkt->setDataFromBlock(line.data.data(), cacheLineSize);
    } else if (pkt->isWrite()) {
        pkt->writeDataToBlock(line.data.data(), cacheLineSize);
        line.dirty = true;
    }
    touch(line);

    if (!pkt->needsResponse()) {
        delete pkt;
        return true;
    }

    pkt->makeTimingResponse();
    if (cpuSidePort.sendTimingResp(pkt))
        return true;

    blockedCpuResponse = pkt;
    return true;
}

void
UACCSelector::enqueueWriteback(const LineKey &key, const LocalLine &line)
{
    if (!line.dirty)
        return;

    RequestPtr req = std::make_shared<Request>(
        key.addr, cacheLineSize, 0, Request::wbRequestorId);
    if (key.secure)
        req->setFlags(Request::SECURE);
    req->setExtension(std::make_shared<UACCRequestExtension>(coreId));
    PacketPtr pkt = new Packet(req, MemCmd::WritebackDirty, cacheLineSize);
    pkt->allocate();
    pkt->setData(line.data.data());
    pendingWritebacks.push_back(pkt);
    if (memRetryKind == MemRetryKind::None && !writebackEvent.scheduled())
        schedule(writebackEvent, clockEdge(Cycles(1)));
}

void
UACCSelector::tryWriteback()
{
    // Do not compete with a normal request that is already waiting for the
    // memory-side port's retry.  The retry callback will reschedule this
    // event after that request has been handed back to the CPU.
    if (memRetryKind == MemRetryKind::CpuRequest)
        return;

    if (pendingWritebacks.empty()) {
        memRetryKind = MemRetryKind::None;
        return;
    }

    PacketPtr pkt = pendingWritebacks.front();
    if (!memSidePort.sendTimingReq(pkt)) {
        memRetryKind = MemRetryKind::Writeback;
        return;
    }

    pendingWritebacks.pop_front();
    memRetryKind = MemRetryKind::None;
    if (!pendingWritebacks.empty() && !writebackEvent.scheduled())
        schedule(writebackEvent, clockEdge(Cycles(1)));
}

void
UACCSelector::installLocalLine(const LineKey &key, const uint8_t *data,
                               bool dirty, bool writable, bool swapped)
{
    auto existing = localLines.find(key);
    if (existing != localLines.end()) {
        existing->second.data.assign(data, data + cacheLineSize);
        existing->second.dirty = dirty;
        existing->second.writable = writable;
        touch(existing->second);
        return;
    }

    const unsigned set = setIndex(key.addr);
    LocalLine *victim = findVictim(set);
    if (victim) {
        for (auto it = localLines.begin(); it != localLines.end(); ++it) {
            if (&it->second != victim)
                continue;
            if (!swapped)
                enqueueWriteback(it->first, it->second);
            localLines.erase(it);
            break;
        }
    }

    LocalLine line;
    line.data.assign(data, data + cacheLineSize);
    line.dirty = dirty;
    line.writable = writable;
    touch(line);
    localLines.emplace(key, std::move(line));
}

bool
UACCSelector::finishTimingResponse(PacketPtr pkt)
{
    if (!pkt->needsResponse()) {
        delete pkt;
        return true;
    }

    pkt->makeTimingResponse();
    if (cpuSidePort.sendTimingResp(pkt))
        return true;

    blockedCpuResponse = pkt;
    return true;
}

bool
UACCSelector::finishTimingResponse(PacketPtr probe, bool remote)
{
    (void)remote;
    auto it = pendingProbes.find(probe);
    if (it == pendingProbes.end())
        return false;

    PendingProbe pending = it->second;
    pendingProbes.erase(it);
    busySets.erase(pending.set);
    // A request to the same set may have been rejected while this probe was
    // outstanding.  The selector now has capacity again, so wake the CPU
    // side explicitly; there is no downstream retry event for a request
    // that never left the selector.

    auto ext = probe->req ?
        probe->req->getExtension<UACCRequestExtension>() : nullptr;
    const bool swapped = ext && ext->swap_applied;
    const bool dirty = ext && ext->remote_hit && ext->remote_dirty;
    const bool writable = !ext || !ext->remote_hit || ext->remote_writable;

    if (!probe->isError()) {
        assert(probe->hasData());
        const LineKey key = lineKey(probe);
        installLocalLine(key, probe->getConstPtr<uint8_t>(), dirty,
                         writable, swapped);
        LocalLine *line = findLocalLine(key);
        assert(line);

        if (pending.original->isWrite()) {
            pending.original->writeDataToBlock(line->data.data(),
                                               cacheLineSize);
            line->dirty = true;
        } else if (pending.original->isRead()) {
            if (!pending.original->hasDataPtr())
                pending.original->allocate();
            pending.original->setDataFromBlock(line->data.data(),
                                               cacheLineSize);
        }
        touch(*line);
    } else {
        pending.original->copyError(probe);
    }

    delete probe;
    if (memRetryKind == MemRetryKind::None && cpuRetryPending) {
        cpuRetryPending = false;
        cpuSidePort.sendRetryReq();
    }
    return finishTimingResponse(pending.original);
}

Tick
UACCSelector::finishAtomicProbe(PacketPtr original, PacketPtr probe,
                                bool remote, Tick latency)
{
    (void)remote;
    auto ext = probe->req ?
        probe->req->getExtension<UACCRequestExtension>() : nullptr;
    if (!probe->isError()) {
        const LineKey key = lineKey(probe);
        const bool swapped = ext && ext->swap_applied;
        const bool dirty = ext && ext->remote_hit && ext->remote_dirty;
        const bool writable = !ext || !ext->remote_hit || ext->remote_writable;
        installLocalLine(key, probe->getConstPtr<uint8_t>(), dirty,
                         writable, swapped);
        LocalLine *line = findLocalLine(key);
        assert(line);
        if (original->isWrite()) {
            original->writeDataToBlock(line->data.data(), cacheLineSize);
            line->dirty = true;
        } else if (original->isRead()) {
            if (!original->hasDataPtr())
                original->allocate();
            original->setDataFromBlock(line->data.data(), cacheLineSize);
        }
        touch(*line);
    } else {
        original->copyError(probe);
    }
    delete probe;
    if (original->needsResponse())
        original->makeAtomicResponse();
    return latency;
}

bool
UACCSelector::recvTimingReq(PacketPtr pkt)
{
    if (memRetryKind != MemRetryKind::None) {
        cpuRetryPending = true;
        return false;
    }

    if (localCacheRequest(pkt)) {
        const LineKey key = lineKey(pkt);
        if (busySets.count(setIndex(key.addr))) {
            cpuRetryPending = true;
            return false;
        }

        if (LocalLine *line = findLocalLine(key)) {
            if (controller)
                controller->recordLocalCacheHit(coreId);
            return serveLocal(pkt, *line);
        }

        return sendProbe(pkt, useRemote(pkt));
    }

    const bool remote = useRemote(pkt);
    const Addr addr = pkt ? pkt->getAddr() : 0;
    const bool trackMiss = pkt && pkt->req && pkt->isDemand() &&
        !pkt->req->isPrefetch() && !pkt->isEviction();
    annotate(pkt, false);

    if (!downstream(remote).sendTimingReq(pkt)) {
        cpuRetryPending = true;
        if (remote && controller)
            controller->recordRemoteBackpressure(coreId);
        if (!remote)
            memRetryKind = MemRetryKind::CpuRequest;
        return false;
    }

    recordForwarded(addr, trackMiss, remote);
    return true;
}

Tick
UACCSelector::recvAtomic(PacketPtr pkt)
{
    if (localCacheRequest(pkt)) {
        const LineKey key = lineKey(pkt);
        if (LocalLine *line = findLocalLine(key)) {
            if (controller)
                controller->recordLocalCacheHit(coreId);
            if (pkt->isRead()) {
                if (!pkt->hasDataPtr())
                    pkt->allocate();
                pkt->setDataFromBlock(line->data.data(), cacheLineSize);
            } else if (pkt->isWrite()) {
                pkt->writeDataToBlock(line->data.data(), cacheLineSize);
                line->dirty = true;
            }
            touch(*line);
            if (pkt->needsResponse())
                pkt->makeAtomicResponse();
            return clockPeriod();
        }

        const bool remote = useRemote(pkt);
        std::shared_ptr<UACCRequestExtension> ext;
        PacketPtr probe = makeProbe(pkt, remote, ext);
        if (controller) {
            controller->recordLocalMiss(coreId, key.addr);
            if (remote)
                controller->recordRemoteLookup(coreId);
        }
        Tick latency = downstream(remote).sendAtomic(probe);
        return finishAtomicProbe(pkt, probe, remote, latency);
    }

    const bool remote = useRemote(pkt);
    const Addr addr = pkt ? pkt->getAddr() : 0;
    const bool trackMiss = pkt && pkt->req && pkt->isDemand() &&
        !pkt->req->isPrefetch() && !pkt->isEviction();
    annotate(pkt, false);
    Tick latency = downstream(remote).sendAtomic(pkt);
    recordForwarded(addr, trackMiss, remote);
    return latency;
}

Tick
UACCSelector::recvAtomicBackdoor(PacketPtr pkt, MemBackdoorPtr &backdoor)
{
    if (localCacheRequest(pkt))
        return recvAtomic(pkt);

    const bool remote = useRemote(pkt);
    const Addr addr = pkt ? pkt->getAddr() : 0;
    const bool trackMiss = pkt && pkt->req && pkt->isDemand() &&
        !pkt->req->isPrefetch() && !pkt->isEviction();
    annotate(pkt, false);
    Tick latency = downstream(remote).sendAtomicBackdoor(pkt, backdoor);
    recordForwarded(addr, trackMiss, remote);
    return latency;
}

void
UACCSelector::recvFunctional(PacketPtr pkt)
{
    if (localCacheRequest(pkt)) {
        const LineKey key = lineKey(pkt);
        if (LocalLine *line = findLocalLine(key)) {
            if (pkt->isRead())
                pkt->setDataFromBlock(line->data.data(), cacheLineSize);
            else if (pkt->isWrite()) {
                pkt->writeDataToBlock(line->data.data(), cacheLineSize);
                line->dirty = true;
            }
            touch(*line);
            return;
        }

        const bool remote = useRemote(pkt);
        std::shared_ptr<UACCRequestExtension> ext;
        PacketPtr probe = makeProbe(pkt, remote, ext);
        downstream(remote).sendFunctional(probe);
        if (probe->hasData()) {
            const LineKey probeKey = lineKey(probe);
            const bool swapped = ext && ext->swap_applied;
            const bool dirty = ext && ext->remote_hit && ext->remote_dirty;
            const bool writable = !ext || !ext->remote_hit ||
                ext->remote_writable;
            installLocalLine(probeKey, probe->getConstPtr<uint8_t>(), dirty,
                             writable, swapped);
            if (pkt->isRead()) {
                if (!pkt->hasDataPtr())
                    pkt->allocate();
                pkt->setDataFromBlock(findLocalLine(probeKey)->data.data(),
                                      cacheLineSize);
            } else if (pkt->isWrite()) {
                LocalLine *line = findLocalLine(probeKey);
                assert(line);
                pkt->writeDataToBlock(line->data.data(), cacheLineSize);
                line->dirty = true;
            }
        }
        delete probe;
        return;
    }

    const bool remote = useRemote(pkt);
    annotate(pkt, false);
    downstream(remote).sendFunctional(pkt);
}

bool
UACCSelector::tryTiming(PacketPtr pkt)
{
    if (memRetryKind != MemRetryKind::None)
        return false;

    if (localCacheRequest(pkt)) {
        const LineKey key = lineKey(pkt);
        if (findLocalLine(key))
            return true;
        if (busySets.count(setIndex(key.addr)))
            return false;
    }
    return downstream(useRemote(pkt)).tryTiming(pkt);
}

bool
UACCSelector::recvTimingResp(PacketPtr pkt, bool remote)
{
    if (atomicSwap && pendingProbes.find(pkt) != pendingProbes.end()) {
        if (remote && controller)
            controller->recordRemoteResponse(coreId, pkt);
        return finishTimingResponse(pkt, remote);
    }

    if (cpuSidePort.sendTimingResp(pkt)) {
        if (remote && controller)
            controller->recordRemoteResponse(coreId, pkt);
        return true;
    }

    if (remote && controller)
        controller->recordRemoteResponseBackpressure(coreId);
    responseRetryRemote = remote;
    return false;
}

void
UACCSelector::recvReqRetry(bool remote)
{
    if (!remote && memRetryKind == MemRetryKind::Writeback) {
        // Re-issue exactly the writeback that previously failed.  Sending
        // only one packet keeps the XBar layer state aligned with the
        // RequestPort retry protocol.
        memRetryKind = MemRetryKind::None;
        tryWriteback();
        if (memRetryKind == MemRetryKind::None && cpuRetryPending) {
            cpuRetryPending = false;
            cpuSidePort.sendRetryReq();
        }
        return;
    }

    if (!remote && memRetryKind == MemRetryKind::CpuRequest) {
        memRetryKind = MemRetryKind::None;
        if (cpuRetryPending) {
            cpuRetryPending = false;
            cpuSidePort.sendRetryReq();
        }
    } else if (remote && memRetryKind == MemRetryKind::None &&
               cpuRetryPending) {
        cpuRetryPending = false;
        cpuSidePort.sendRetryReq();
    }

    if (!remote && memRetryKind == MemRetryKind::None &&
        !pendingWritebacks.empty() && !writebackEvent.scheduled())
        schedule(writebackEvent, clockEdge(Cycles(1)));
}

bool
UACCSelector::recvTimingSnoopResp(PacketPtr pkt)
{
    return memSidePort.sendTimingSnoopResp(pkt);
}

void
UACCSelector::recvRespRetry()
{
    if (blockedCpuResponse) {
        if (cpuSidePort.sendTimingResp(blockedCpuResponse))
            blockedCpuResponse = nullptr;
        return;
    }
    downstream(responseRetryRemote).sendRetryResp();
}

void
UACCSelector::recvTimingSnoopReq(PacketPtr pkt, bool remote)
{
    if (!remote)
        cpuSidePort.sendTimingSnoopReq(pkt);
}

Tick
UACCSelector::recvAtomicSnoop(PacketPtr pkt, bool remote)
{
    return remote ? 0 : cpuSidePort.sendAtomicSnoop(pkt);
}

void
UACCSelector::recvFunctionalSnoop(PacketPtr pkt, bool remote)
{
    if (!remote)
        cpuSidePort.sendFunctionalSnoop(pkt);
}

void
UACCSelector::recvRetrySnoopResp(bool remote)
{
    if (!remote)
        cpuSidePort.sendRetrySnoopResp();
}

bool
UACCSelector::CpuSidePort::recvTimingReq(PacketPtr pkt)
{
    return selector.recvTimingReq(pkt);
}

Tick
UACCSelector::CpuSidePort::recvAtomic(PacketPtr pkt)
{
    return selector.recvAtomic(pkt);
}

Tick
UACCSelector::CpuSidePort::recvAtomicBackdoor(PacketPtr pkt,
                                               MemBackdoorPtr &backdoor)
{
    return selector.recvAtomicBackdoor(pkt, backdoor);
}

void
UACCSelector::CpuSidePort::recvFunctional(PacketPtr pkt)
{
    selector.recvFunctional(pkt);
}

bool
UACCSelector::CpuSidePort::tryTiming(PacketPtr pkt)
{
    return selector.tryTiming(pkt);
}

bool
UACCSelector::CpuSidePort::recvTimingSnoopResp(PacketPtr pkt)
{
    return selector.recvTimingSnoopResp(pkt);
}

void
UACCSelector::CpuSidePort::recvRespRetry()
{
    selector.recvRespRetry();
}

AddrRangeList
UACCSelector::CpuSidePort::getAddrRanges() const
{
    return selector.memSidePort.getAddrRanges();
}

bool
UACCSelector::DownstreamPort::recvTimingResp(PacketPtr pkt)
{
    return selector.recvTimingResp(pkt, remote);
}

void
UACCSelector::DownstreamPort::recvReqRetry()
{
    selector.recvReqRetry(remote);
}

void
UACCSelector::DownstreamPort::recvTimingSnoopReq(PacketPtr pkt)
{
    selector.recvTimingSnoopReq(pkt, remote);
}

Tick
UACCSelector::DownstreamPort::recvAtomicSnoop(PacketPtr pkt)
{
    return selector.recvAtomicSnoop(pkt, remote);
}

void
UACCSelector::DownstreamPort::recvFunctionalSnoop(PacketPtr pkt)
{
    selector.recvFunctionalSnoop(pkt, remote);
}

void
UACCSelector::DownstreamPort::recvRetrySnoopResp()
{
    selector.recvRetrySnoopResp(remote);
}

} // namespace gem5
