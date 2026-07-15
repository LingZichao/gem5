#include "mem/uacc/uacc_cache.hh"

#include <cstring>

#include "mem/uacc/uacc_controller.hh"
#include "mem/uacc/uacc_extension.hh"

namespace gem5
{

UACCCache::UACCCache(const Params &p)
    : NoncoherentCache(p), controller(p.controller)
{
}

void
UACCCache::applyAtomicSwap(
    PacketPtr pkt, CacheBlk *blk,
    const std::shared_ptr<UACCRequestExtension> &ext)
{
    if (!blk || !blk->isValid() || !ext->victim_valid ||
        ext->victim_data.size() != blkSize) {
        return;
    }

    // The normal cache access has already copied the remote line into pkt.
    // Exchange the remote tag/data entry only after that copy so the packet
    // still carries the line being promoted to the host.
    RequestPtr victim_req = std::make_shared<Request>(
        ext->victim_addr, blkSize, 0, Request::wbRequestorId);
    if (ext->victim_secure)
        victim_req->setFlags(Request::SECURE);
    victim_req->setExtension(
        std::make_shared<UACCRequestExtension>(ext->core_id));

    PacketPtr victim_pkt = new Packet(
        victim_req,
        ext->victim_dirty ? MemCmd::WritebackDirty : MemCmd::WritebackClean,
        blkSize);
    victim_pkt->allocate();
    victim_pkt->setData(ext->victim_data.data());

    invalidateBlock(blk);
    tags->insertBlock(victim_pkt, blk);
    blk->setCoherenceBits(CacheBlk::ReadableBit);
    if (ext->victim_writable)
        blk->setCoherenceBits(CacheBlk::WritableBit);
    if (ext->victim_dirty)
        blk->setCoherenceBits(CacheBlk::DirtyBit);
    blk->setWhenReady(clockEdge(fillLatency));
    updateBlockData(blk, victim_pkt, false);

    ext->swap_applied = true;
    if (controller)
        controller->recordAtomicSwap(ext->core_id);

    delete victim_pkt;
}

bool
UACCCache::access(PacketPtr pkt, CacheBlk *&blk, Cycles &lat,
                  PacketList &writebacks)
{
    const bool hit = NoncoherentCache::access(pkt, blk, lat, writebacks);

    std::shared_ptr<UACCRequestExtension> extension;
    if (pkt && pkt->req)
        extension = pkt->req->getExtension<UACCRequestExtension>();

    if (extension && extension->atomic_swap_probe) {
        extension->remote_hit = hit;
        if (hit) {
            // Preserve the remote line's coherence state when it is
            // promoted into the selector's host-side line store.  Without
            // this, a dirty remote line could be exchanged successfully but
            // later evicted from the host side as if it were clean.
            extension->remote_dirty = blk->isSet(CacheBlk::DirtyBit);
            extension->remote_writable = blk->isSet(CacheBlk::WritableBit);
            if (controller)
                controller->recordAtomicSwapHit(extension->core_id);
            applyAtomicSwap(pkt, blk, extension);
        }
    }

    if (controller) {
        unsigned coreId = 0;
        if (extension)
            coreId = extension->core_id;
        controller->recordRemoteAccess(coreId, hit, pkt);
    }

    return hit;
}

} // namespace gem5
