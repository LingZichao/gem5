#ifndef __MEM_SERIAL_LINK_MODEL_HH__
#define __MEM_SERIAL_LINK_MODEL_HH__

#include "base/intmath.hh"
#include "base/types.hh"

namespace gem5
{
namespace serial_link
{

inline Cycles
serializationCycles(unsigned payload_size, bool has_size,
                    unsigned num_lanes, unsigned link_speed)
{
    if (!has_size || num_lanes == 0 || link_speed == 0)
        return Cycles(0);
    return Cycles(divCeil(static_cast<uint64_t>(payload_size) * 8,
                          static_cast<uint64_t>(num_lanes) * link_speed));
}

} // namespace serial_link
} // namespace gem5

#endif // __MEM_SERIAL_LINK_MODEL_HH__
