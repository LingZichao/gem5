#ifndef __MEM_UACC_UACC_CACHE_HH__
#define __MEM_UACC_UACC_CACHE_HH__

#include <memory>

#include "mem/cache/noncoherent_cache.hh"
#include "params/UACCCache.hh"

namespace gem5
{

class UACCController;
class UACCRequestExtension;

class UACCCache : public NoncoherentCache
{
  private:
    UACCController *controller;

    void applyAtomicSwap(PacketPtr pkt, CacheBlk *blk,
                         const std::shared_ptr<UACCRequestExtension> &ext);

  protected:
    bool access(PacketPtr pkt, CacheBlk *&blk, Cycles &lat,
                PacketList &writebacks) override;

  public:
    PARAMS(UACCCache);
    UACCCache(const Params &p);
};

} // namespace gem5

#endif // __MEM_UACC_UACC_CACHE_HH__
