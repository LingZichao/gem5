#ifndef __MEM_UACC_UACC_EXTENSION_HH__
#define __MEM_UACC_UACC_EXTENSION_HH__

#include <cstdint>
#include <memory>
#include <vector>

#include "base/extensible.hh"
#include "base/types.hh"
#include "mem/request.hh"

namespace gem5
{

class UACCRequestExtension : public Extension<Request, UACCRequestExtension>
{
  public:
    enum class QueueClass : uint8_t
    {
        Request = 0,
        HitResponse,
        MissResponse,
        Writeback,
        Other,
        Count,
    };

    struct QueueTiming
    {
        Tick enqueue = 0;
        Tick service_start = 0;
        Tick service_ticks = 0;
        Tick fixed_ticks = 0;
        unsigned occupancy = 0;
        bool valid = false;
        QueueClass queue_class = QueueClass::Other;
        uint64_t buffer_full_events = 0;
    };

    explicit UACCRequestExtension(unsigned _core_id = 0) : core_id(_core_id)
    {}

    std::unique_ptr<ExtensionBase> clone() const override
    {
        return std::make_unique<UACCRequestExtension>(*this);
    }

    unsigned core_id;

    // A host-side probe carries the victim that may be exchanged with a
    // remote hit.  The extension is cloned with Request, so this metadata
    // remains attached when a cache creates a downstream packet.
    bool atomic_swap_probe = false;
    bool victim_valid = false;
    Addr victim_addr = 0;
    bool victim_secure = false;
    bool victim_dirty = false;
    bool victim_writable = true;
    std::vector<uint8_t> victim_data;

    // Filled by UACCCache while processing the remote probe.
    bool remote_hit = false;
    bool swap_applied = false;
    bool remote_dirty = false;
    bool remote_writable = true;

    // Filled by SerialLink at the actual request and response queueing
    // points.  The two directions are separate queueing domains.
    QueueTiming request_queue;
    QueueTiming response_queue;
};

} // namespace gem5

#endif // __MEM_UACC_UACC_EXTENSION_HH__
