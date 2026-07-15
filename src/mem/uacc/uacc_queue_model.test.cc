#include <gtest/gtest.h>

#include "mem/serial_link_model.hh"
#include "mem/uacc/uacc_queue_model.hh"

namespace gem5
{

TEST(UACCQueueModelTest, WaitRemovesFixedDelayExactlyOnce)
{
    EXPECT_EQ(uacc::observedQueueWait(100, 140, 20), 20);
    EXPECT_EQ(uacc::observedQueueWait(100, 110, 20), 0);
}

TEST(UACCQueueModelTest, Section14UsesWindowSums)
{
    EXPECT_DOUBLE_EQ(uacc::section14QueueCost(2.0, 20.0, 200.0, 1.0,
                                              100.0), 2.5);
}

TEST(UACCQueueModelTest, SimultaneousArrivalsSaturateCA2)
{
    EXPECT_DOUBLE_EQ(uacc::arrivalCa2(3, 0.0, 0.0, 1.0, 1024.0),
                     1024.0);
    EXPECT_DOUBLE_EQ(uacc::arrivalCa2(0, 0.0, 0.0, 2.0, 1024.0), 2.0);
}

TEST(UACCQueueModelTest, SerialLinkIgnoresInvalidPacketSize)
{
    EXPECT_EQ(static_cast<uint64_t>(serial_link::serializationCycles(
                  64, false, 1, 8)), 0);
    EXPECT_EQ(static_cast<uint64_t>(serial_link::serializationCycles(
                  64, true, 1, 8)), 64);
    EXPECT_EQ(static_cast<uint64_t>(serial_link::serializationCycles(
                  4, true, 1, 8)), 4);
}

} // namespace gem5
