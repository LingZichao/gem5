#ifndef __MEM_UACC_UACC_PARTITION_HH__
#define __MEM_UACC_UACC_PARTITION_HH__

#include <cstdint>
#include <unordered_map>
#include <vector>

#include "mem/cache/tags/partitioning_policies/base_pp.hh"
#include "mem/cache/tags/partitioning_policies/partition_manager.hh"
#include "mem/packet.hh"
#include "params/UACCCapacityPolicy.hh"
#include "params/UACCPartitionManager.hh"

namespace gem5
{

class UACCCapacityPolicy : public partitioning_policy::BasePartitioningPolicy
{
  public:
    PARAMS(UACCCapacityPolicy);
    UACCCapacityPolicy(const Params &p);

    void filterByPartition(std::vector<ReplaceableEntry *> &entries,
                           uint64_t partition_id) const override;
    void notifyAcquire(uint64_t partition_id) override;
    void notifyRelease(uint64_t partition_id) override;

    void setAllocation(uint64_t partition_id, unsigned ways);
    unsigned allocation(uint64_t partition_id) const;

  private:
    const uint64_t numSets;
    const uint64_t maxBlocks;
    std::unordered_map<uint64_t, uint64_t> maxPartitionBlocks;
    std::unordered_map<uint64_t, uint64_t> currentPartitionBlocks;
};

class UACCPartitionManager : public partitioning_policy::PartitionManager
{
  public:
    PARAMS(UACCPartitionManager);
    UACCPartitionManager(const Params &p);

    uint64_t readPacketPartitionID(PacketPtr pkt) const override;

    void setAllocation(uint64_t partition_id, unsigned ways);
    unsigned allocation(uint64_t partition_id) const;

  private:
    UACCCapacityPolicy *capacityPolicy;
};

} // namespace gem5

#endif // __MEM_UACC_UACC_PARTITION_HH__
