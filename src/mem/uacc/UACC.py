# Copyright (c) 2026
# All rights reserved.

from m5.objects.Cache import NoncoherentCache
from m5.objects.ClockedObject import ClockedObject
from m5.objects.PartitioningPolicies import (
    BasePartitioningPolicy,
    PartitionManager,
)
from m5.params import *
from m5.proxy import *


class UACCCapacityPolicy(BasePartitioningPolicy):
    type = "UACCCapacityPolicy"
    cxx_header = "mem/uacc/uacc_partition.hh"
    cxx_class = "gem5::UACCCapacityPolicy"

    cache_size = Param.MemorySize("Maximum UACC cache capacity")
    block_size = Param.Unsigned(64, "Cache line size")
    num_sets = Param.Unsigned(1, "Number of UACC cache sets")
    partition_ids = VectorParam.UInt64([], "Initial partition IDs")
    allocations = VectorParam.Unsigned([], "Initial allocation in ways")


class UACCPartitionManager(PartitionManager):
    type = "UACCPartitionManager"
    cxx_header = "mem/uacc/uacc_partition.hh"
    cxx_class = "gem5::UACCPartitionManager"

    capacity_policy = Param.UACCCapacityPolicy(
        NULL, "Runtime capacity policy controlled by UACCController"
    )


class UACCController(ClockedObject):
    type = "UACCController"
    cxx_header = "mem/uacc/uacc_controller.hh"
    cxx_class = "gem5::UACCController"

    num_cores = Param.Unsigned("Number of cores using UACC")
    cache_line_size = Param.Unsigned(64, "Cache line size used by the ATD")
    profile_interval = Param.Cycles(
        100000, "Number of controller cycles per profiling window"
    )
    sampled_sets = Param.Unsigned(32, "Number of sampled ATD sets")
    profile_depth = Param.Unsigned(64, "Maximum ATD reuse distance")
    base_ways = Param.Unsigned(16, "Local cache associativity in ways")
    max_remote_ways = Param.Unsigned(
        0, "Total remote ways available to the allocator"
    )
    lookahead_deltas = VectorParam.Unsigned(
        [1, 2, 4], "Lookahead increments considered by the allocator"
    )
    lower_miss_penalty_cycles = Param.Float(
        100.0, "Estimated cycles saved by avoiding a lower-level miss"
    )
    distance_ns = VectorParam.Float([], "Per-core host-to-UACC distance")
    distance_discount_per_ns = Param.Float(
        0.0053, "Distance discount per nanosecond"
    )
    d2d_rtt = Param.Latency("10ns", "D2D round-trip latency")
    d2d_bandwidth = Param.MemoryBandwidth(
        "128GiB/s", "D2D payload bandwidth; internally ticks per byte"
    )
    response_flits = Param.Unsigned(1, "D2D flits per cache response")
    allocation_policy = Param.String(
        "congestion", "static, greedy, distance, or congestion"
    )
    initial_allocation = VectorParam.Unsigned(
        [], "Initial or static allocation in ways per core"
    )
    dynamic_allocation = Param.Bool(
        True, "Periodically recompute the allocation"
    )
    partition_manager = Param.UACCPartitionManager(
        NULL, "Partition manager for the remote cache"
    )


class UACCSelector(ClockedObject):
    type = "UACCSelector"
    cxx_header = "mem/uacc/uacc_selector.hh"
    cxx_class = "gem5::UACCSelector"

    cpu_side_port = ResponsePort("Port connected to the local cache")
    remote_side_port = RequestPort("Port connected to the D2D path")
    mem_side_port = RequestPort("Port connected to the ordinary memory path")

    core_id = Param.Unsigned("Core ID represented by this selector")
    cache_line_size = Param.Unsigned(
        64, "Host-side local cache line size"
    )
    local_size = Param.MemorySize(
        "1KiB", "Host-side local cache capacity used by the swap model"
    )
    local_assoc = Param.Unsigned(
        2, "Host-side local cache associativity used by the swap model"
    )
    atomic_swap = Param.Bool(
        False,
        "Perform parallel host/UACC probes and atomic line exchange on hits",
    )
    controller = Param.UACCController(
        NULL, "Controller selecting the remote or bypass path"
    )


class UACCCache(NoncoherentCache):
    type = "UACCCache"
    cxx_header = "mem/uacc/uacc_cache.hh"
    cxx_class = "gem5::UACCCache"

    controller = Param.UACCController(
        NULL, "Controller receiving remote hit/miss notifications"
    )
