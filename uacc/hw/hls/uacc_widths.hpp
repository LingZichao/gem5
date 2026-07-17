#ifndef UACC_SECTION14_WIDTHS_HPP
#define UACC_SECTION14_WIDTHS_HPP

// -1 preserves the original wide prototype.  The controlled range sweep uses
// 0 for the wide Q4.4 control, 16 for the deliberately lossy 100K profile,
// 100 for the safe Tmax=100K profile, and 1000 for Tmax=1M.
#ifndef UACC_HLS_RANGE_PROFILE
#define UACC_HLS_RANGE_PROFILE -1
#endif

namespace uacc_hls
{

#if UACC_HLS_RANGE_PROFILE == 16
constexpr unsigned long long ProfileMaxWindowCycles = 100000ULL;
constexpr unsigned long long ProfileMaxPacketsPerDomain = 100000ULL;
constexpr unsigned CountBits = 17;
constexpr unsigned WindowBits = 17;
constexpr unsigned ArrivalCountBits = 17;
constexpr unsigned ArrivalSumBits = 18;
constexpr unsigned ArrivalSquareSumBits = 35;
constexpr unsigned WaitSumBits = 29;
constexpr unsigned EventCountBits = 17;
constexpr unsigned ServiceBits = 6;
constexpr unsigned ServiceSquareBits = 11;
constexpr unsigned BusySumBits = 16;
constexpr unsigned ServiceSecondSumBits = 20;
constexpr unsigned FixedLatencyIntegerBits = 10;
constexpr unsigned CostIntegerBits = 20;
constexpr unsigned TotalCostIntegerBits = 23;
constexpr unsigned SharedDividerIntegerBits = 51;
constexpr unsigned SharedDividerDenominatorBits = 36;
constexpr unsigned ProfileRhoFractionBits = 8;
#elif UACC_HLS_RANGE_PROFILE == 100
constexpr unsigned long long ProfileMaxWindowCycles = 100000ULL;
constexpr unsigned long long ProfileMaxPacketsPerDomain = 100000ULL;
constexpr unsigned CountBits = 17;
constexpr unsigned WindowBits = 17;
constexpr unsigned ArrivalCountBits = 17;
constexpr unsigned ArrivalSumBits = 18;
constexpr unsigned ArrivalSquareSumBits = 35;
constexpr unsigned WaitSumBits = 29;
constexpr unsigned EventCountBits = 17;
constexpr unsigned ServiceBits = 6;
constexpr unsigned ServiceSquareBits = 11;
constexpr unsigned BusySumBits = 22;
constexpr unsigned ServiceSecondSumBits = 27;
constexpr unsigned FixedLatencyIntegerBits = 10;
constexpr unsigned CostIntegerBits = 31;
constexpr unsigned TotalCostIntegerBits = 34;
constexpr unsigned SharedDividerIntegerBits = 51;
constexpr unsigned SharedDividerDenominatorBits = 36;
constexpr unsigned ProfileRhoFractionBits = 8;
#elif UACC_HLS_RANGE_PROFILE == 1000
constexpr unsigned long long ProfileMaxWindowCycles = 1000000ULL;
constexpr unsigned long long ProfileMaxPacketsPerDomain = 1000000ULL;
constexpr unsigned CountBits = 20;
constexpr unsigned WindowBits = 20;
constexpr unsigned ArrivalCountBits = 20;
constexpr unsigned ArrivalSumBits = 21;
constexpr unsigned ArrivalSquareSumBits = 41;
constexpr unsigned WaitSumBits = 32;
constexpr unsigned EventCountBits = 20;
constexpr unsigned ServiceBits = 6;
constexpr unsigned ServiceSquareBits = 11;
constexpr unsigned BusySumBits = 25;
constexpr unsigned ServiceSecondSumBits = 30;
constexpr unsigned FixedLatencyIntegerBits = 10;
constexpr unsigned CostIntegerBits = 35;
constexpr unsigned TotalCostIntegerBits = 38;
constexpr unsigned SharedDividerIntegerBits = 61;
constexpr unsigned SharedDividerDenominatorBits = 42;
constexpr unsigned ProfileRhoFractionBits = 8;
#else
constexpr unsigned long long ProfileMaxWindowCycles = 1000000ULL;
constexpr unsigned long long ProfileMaxPacketsPerDomain = 1000000ULL;
constexpr unsigned CountBits = 32;
constexpr unsigned WindowBits = 40;
constexpr unsigned ArrivalCountBits = 32;
constexpr unsigned ArrivalSumBits = 64;
constexpr unsigned ArrivalSquareSumBits = 96;
constexpr unsigned WaitSumBits = 64;
constexpr unsigned EventCountBits = 32;
constexpr unsigned ServiceBits = 24;
constexpr unsigned ServiceSquareBits = 48;
constexpr unsigned BusySumBits = 56;
constexpr unsigned ServiceSecondSumBits = 80;
constexpr unsigned FixedLatencyIntegerBits = 40;
constexpr unsigned CostIntegerBits = 80;
constexpr unsigned TotalCostIntegerBits = 84;
#if UACC_HLS_RANGE_PROFILE == 0
constexpr unsigned SharedDividerIntegerBits = 128;
constexpr unsigned SharedDividerDenominatorBits = 128;
constexpr unsigned ProfileRhoFractionBits = 8;
#else
constexpr unsigned SharedDividerIntegerBits = 128;
constexpr unsigned SharedDividerDenominatorBits = 128;
constexpr unsigned ProfileRhoFractionBits = 0;
#endif
#endif

constexpr unsigned ArrivalCycleBits = 40;

} // namespace uacc_hls

#endif
