#ifndef __MEM_UACC_UACC_QUEUE_MODEL_HH__
#define __MEM_UACC_UACC_QUEUE_MODEL_HH__

#include <algorithm>
#include <cstdint>
#include <limits>

#include "base/types.hh"

namespace gem5
{
namespace uacc
{

inline double
arrivalCa2(uint64_t samples, long double sum, long double square_sum,
           double previous, double maximum)
{
    if (samples == 0)
        return previous;
    if (sum <= 0.0)
        return maximum;
    const long double ratio = samples * square_sum / (sum * sum);
    return std::min(maximum,
                    std::max(0.0, static_cast<double>(ratio - 1.0)));
}

/** Queue wait with fixed delay removed and unsigned arithmetic clipped. */
inline Tick
observedQueueWait(Tick enqueue, Tick service_start, Tick fixed_ticks)
{
    const Tick fixed_end = fixed_ticks >
        std::numeric_limits<Tick>::max() - enqueue ?
        std::numeric_limits<Tick>::max() : enqueue + fixed_ticks;
    return service_start > fixed_end ? service_start - fixed_end : 0;
}

/** Section 14 direct queue cost in cycles per profiling window. */
inline double
section14QueueCost(double r0, double r1, double r2, double ca2, double window)
{
    if (r0 <= 0.0 || r1 <= 0.0 || window <= r1)
        return 0.0;
    return (r1 * r1 * (ca2 - 1.0) + r0 * r2) /
        (2.0 * (window - r1));
}

} // namespace uacc
} // namespace gem5

#endif // __MEM_UACC_UACC_QUEUE_MODEL_HH__
