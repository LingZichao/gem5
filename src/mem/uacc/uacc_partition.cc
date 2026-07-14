#include "mem/uacc/uacc_partition.hh"

#include <algorithm>

#include "base/logging.hh"
#include "mem/cache/replacement_policies/replaceable_entry.hh"
#include "mem/cache/cache_blk.hh"
#include "mem/uacc/uacc_extension.hh"
#include "params/UACCPartitionManager.hh"

namespace gem5
{

UACCCapacityPolicy::UACCCapacityPolicy(const Params &p)
    : BasePartitioningPolicy(p),
      numSets(std::max<uint64_t>(1, p.num_sets)),
      maxBlocks(p.cache_size / p.block_size)
{
    fatal_if(p.partition_ids.size() != p.allocations.size(),
             "UACC partition_ids and allocations must have equal lengths");

    for (size_t i = 0; i < p.partition_ids.size(); ++i) {
        setAllocation(p.partition_ids[i], p.allocations[i]);
    }
}

void
UACCCapacityPolicy::setAllocation(uint64_t partition_id, unsigned ways)
{
    const uint64_t blocks = std::min<uint64_t>(maxBlocks, ways * numSets);
    maxPartitionBlocks[partition_id] = blocks;
    currentPartitionBlocks.try_emplace(partition_id, 0);
}

unsigned
UACCCapacityPolicy::allocation(uint64_t partition_id) const
{
    auto it = maxPartitionBlocks.find(partition_id);
    return it == maxPartitionBlocks.end() ? 0 : it->second / numSets;
}

void
UACCCapacityPolicy::filterByPartition(
    std::vector<ReplaceableEntry *> &entries, uint64_t partition_id) const
{
    auto max_it = maxPartitionBlocks.find(partition_id);
    if (max_it == maxPartitionBlocks.end())
        return;

    const uint64_t current = currentPartitionBlocks.count(partition_id) ?
        currentPartitionBlocks.at(partition_id) : 0;
    const uint64_t maximum = max_it->second;

    if (maximum == 0 || current >= maximum) {
        entries.erase(std::remove_if(entries.begin(), entries.end(),
            [partition_id](ReplaceableEntry *entry) {
                auto *blk = static_cast<CacheBlk *>(entry);
                return !blk->isValid() || blk->getPartitionId() != partition_id;
            }), entries.end());
    }
}

void
UACCCapacityPolicy::notifyAcquire(uint64_t partition_id)
{
    ++currentPartitionBlocks[partition_id];
}

void
UACCCapacityPolicy::notifyRelease(uint64_t partition_id)
{
    auto it = currentPartitionBlocks.find(partition_id);
    if (it != currentPartitionBlocks.end() && it->second > 0)
        --it->second;
}

UACCPartitionManager::UACCPartitionManager(const Params &p)
    : PartitionManager(p), capacityPolicy(p.capacity_policy)
{
}

uint64_t
UACCPartitionManager::readPacketPartitionID(PacketPtr pkt) const
{
    if (pkt && pkt->req) {
        auto extension = pkt->req->getExtension<UACCRequestExtension>();
        if (extension)
            return extension->core_id;
    }
    return PartitionManager::readPacketPartitionID(pkt);
}

void
UACCPartitionManager::setAllocation(uint64_t partition_id, unsigned ways)
{
    if (capacityPolicy)
        capacityPolicy->setAllocation(partition_id, ways);
}

unsigned
UACCPartitionManager::allocation(uint64_t partition_id) const
{
    return capacityPolicy ? capacityPolicy->allocation(partition_id) : 0;
}

} // namespace gem5
