# Copyright (c) 2026
# All rights reserved.

"""Minimal classic-timing UACC experiment.

The configuration intentionally uses traffic generators instead of a CPU so
that the UACC routing, remote-cache capacity policy, and controller can be
tested in isolation.  Each generator scans a small working set twice; with a
non-zero static allocation, the second scan can hit in the remote cache.
"""

import argparse

import m5
from m5.objects import *
from m5.ticks import setGlobalFrequency
from m5.util.convert import toMemorySize


D2D_INTERFACES = {
    # Ranges from Table 3 of acm-sigconf.pdf. The nominal point is the
    # midpoint of each range; low/high select the corresponding endpoint.
    "AIB": {
        "rtt_ns": (10.0, 15.0),
        "bandwidth_gb_s": (10.0, 120.0),
        "buffer_flits": (8, 16),
    },
    "BoW": {
        "rtt_ns": (8.0, 12.0),
        "bandwidth_gb_s": (50.0, 250.0),
        "buffer_flits": (16, 32),
    },
    "EMIB": {
        "rtt_ns": (6.0, 10.0),
        "bandwidth_gb_s": (100.0, 300.0),
        "buffer_flits": (24, 32),
    },
    "UCIe": {
        "rtt_ns": (4.0, 8.0),
        "bandwidth_gb_s": (128.0, 512.0),
        "buffer_flits": (24, 64),
    },
}


def parse_allocations(value, num_cores):
    allocations = [int(item, 0) for item in value.split(",") if item]
    if len(allocations) == 1:
        allocations *= num_cores
    if len(allocations) != num_cores:
        raise argparse.ArgumentTypeError(
            "initial allocation must contain one value or one per core"
        )
    if any(item < 0 for item in allocations):
        raise argparse.ArgumentTypeError("allocations must be non-negative")
    return allocations


parser = argparse.ArgumentParser(
    formatter_class=argparse.ArgumentDefaultsHelpFormatter
)
parser.add_argument("--num-cores", type=int, default=1)
parser.add_argument(
    "--uacc-policy",
    choices=["static", "greedy", "distance", "congestion"],
    default="static",
)
parser.add_argument(
    "--uacc-queue-model",
    choices=["mg1", "gg1-feedback", "section14"],
    default="mg1",
    help="Queue-cost model used by the congestion allocator",
)
parser.add_argument("--initial-allocation", default="2")
parser.add_argument("--max-remote-ways", type=int, default=None)
parser.add_argument("--remote-size", default="4KiB")
parser.add_argument("--remote-assoc", type=int, default=4)
parser.add_argument("--working-set", default="2KiB")
parser.add_argument("--memory-size", default="1MiB")
parser.add_argument("--cache-line-size", type=int, default=64)
parser.add_argument(
    "--request-size",
    type=int,
    default=None,
    help="Traffic request size; defaults to one quarter of a cache line to avoid "
         "whole-line-write MSHR semantics in NoncoherentCache",
)
parser.add_argument("--profile-interval", type=int, default=1000)
parser.add_argument("--profile-depth", type=int, default=64)
parser.add_argument("--sampled-sets", type=int, default=16)
parser.add_argument("--min-arrival-samples", type=int, default=32)
parser.add_argument("--rho-max", type=float, default=0.90)
parser.add_argument("--feedback-beta-max", type=float, default=4.0)
parser.add_argument("--queue-occupancy-threshold", type=int, default=0)
parser.add_argument("--backpressure-threshold", type=int, default=0)
parser.add_argument("--contraction-windows", type=int, default=3)
parser.add_argument("--distance-ns", type=float, default=10.0)
parser.add_argument(
    "--atomic-swap",
    action="store_true",
    help="Enable host-side local cache, parallel probes, and atomic line swap",
)
parser.add_argument("--local-size", default="1KiB")
parser.add_argument("--local-assoc", type=int, default=2)
parser.add_argument(
    "--d2d-interface",
    choices=["custom"] + list(D2D_INTERFACES),
    default="UCIe",
    help="D2D tier from Table 3, or custom for explicit values",
)
parser.add_argument(
    "--d2d-point",
    choices=["low", "nominal", "high"],
    default="nominal",
    help="Endpoint or midpoint selected from the paper's parameter range",
)
parser.add_argument("--d2d-rtt-ns", type=float, default=None)
parser.add_argument("--d2d-bandwidth-gb-s", type=float, default=None)
parser.add_argument("--d2d-buffer-flits", type=int, default=None)
parser.add_argument("--period", type=int, default=1000)
parser.add_argument("--phase-ticks", type=int, default=1_000_000)
parser.add_argument("--passes", type=int, default=2)
parser.add_argument("--read-percent", type=int, default=100)
parser.add_argument("--max-outstanding", type=int, default=8)
parser.add_argument("--maxtick", type=int, default=10_000_000)
args = parser.parse_args()

if args.num_cores < 1:
    parser.error("--num-cores must be positive")
if args.remote_assoc < 1:
    parser.error("--remote-assoc must be positive")
if args.local_assoc < 1:
    parser.error("--local-assoc must be positive")
if args.min_arrival_samples < 1:
    parser.error("--min-arrival-samples must be positive")
if not 0.0 < args.rho_max < 1.0:
    parser.error("--rho-max must be between zero and one")
if args.feedback_beta_max < 1.0:
    parser.error("--feedback-beta-max must be at least one")
if args.queue_occupancy_threshold < 0:
    parser.error("--queue-occupancy-threshold must be non-negative")
if args.backpressure_threshold < 0:
    parser.error("--backpressure-threshold must be non-negative")
if args.contraction_windows < 1:
    parser.error("--contraction-windows must be positive")
request_size = args.request_size or max(1, args.cache_line_size // 4)
if request_size < 1 or request_size > args.cache_line_size:
    parser.error("request-size must be between 1 and cache-line-size")
if args.cache_line_size % request_size:
    parser.error("request-size must divide cache-line-size")
if args.passes < 1:
    parser.error("--passes must be positive")
if not 0 <= args.read_percent <= 100:
    parser.error("--read-percent must be between 0 and 100")

if args.d2d_interface == "custom":
    d2d_rtt_ns = 10.0
    d2d_bandwidth_gb_s = 128.0
    d2d_buffer_flits = 8
else:
    profile = D2D_INTERFACES[args.d2d_interface]
    point_index = {"low": 0, "nominal": None, "high": 1}[args.d2d_point]

    def select_point(bounds, integer=False):
        if point_index is None:
            value = (bounds[0] + bounds[1]) / 2.0
        else:
            value = bounds[point_index]
        return int(round(value)) if integer else value

    d2d_rtt_ns = select_point(profile["rtt_ns"])
    d2d_bandwidth_gb_s = select_point(profile["bandwidth_gb_s"])
    d2d_buffer_flits = select_point(profile["buffer_flits"], integer=True)

if args.d2d_rtt_ns is not None:
    d2d_rtt_ns = args.d2d_rtt_ns
if args.d2d_bandwidth_gb_s is not None:
    d2d_bandwidth_gb_s = args.d2d_bandwidth_gb_s
if args.d2d_buffer_flits is not None:
    d2d_buffer_flits = args.d2d_buffer_flits
if d2d_rtt_ns <= 0.0:
    parser.error("D2D RTT must be positive")
if d2d_bandwidth_gb_s <= 0.0:
    parser.error("D2D bandwidth must be positive")
if d2d_buffer_flits < 1:
    parser.error("D2D buffer depth must be positive")

remote_bytes = toMemorySize(args.remote_size)
working_set_bytes = toMemorySize(args.working_set)
memory_bytes = toMemorySize(args.memory_size)
local_bytes = toMemorySize(args.local_size)
if remote_bytes % (args.cache_line_size * args.remote_assoc):
    parser.error("remote-size must be divisible by line size times assoc")
if working_set_bytes % args.cache_line_size:
    parser.error("working-set must be divisible by cache-line-size")
if working_set_bytes % request_size:
    parser.error("working-set must be divisible by request-size")
if local_bytes % (args.cache_line_size * args.local_assoc):
    parser.error("local-size must be divisible by line size times assoc")
if working_set_bytes * args.num_cores > memory_bytes:
    parser.error("working sets do not fit in memory-size")

remote_sets = remote_bytes // (args.cache_line_size * args.remote_assoc)
if args.max_remote_ways is None:
    args.max_remote_ways = args.remote_assoc
if args.max_remote_ways < 0:
    parser.error("--max-remote-ways must be non-negative")

initial_allocation = parse_allocations(
    args.initial_allocation, args.num_cores
)
if sum(initial_allocation) > args.max_remote_ways:
    parser.error("initial allocation exceeds max-remote-ways")

setGlobalFrequency("1THz")
system = System(
    cache_line_size=args.cache_line_size,
    mem_ranges=[AddrRange(args.memory_size)],
)
system.mem_mode = "timing"
system.clk_domain = SrcClockDomain(
    clock="1GHz", voltage_domain=VoltageDomain()
)

system.membus = NoncoherentXBar(
    width=16, frontend_latency=1, forward_latency=1, response_latency=1
)
system.mem_ctrl = SimpleMemory(
    range=AddrRange(args.memory_size), latency="30ns", bandwidth="8GiB/s"
)
system.mem_ctrl.port = system.membus.mem_side_ports

capacity_policy = UACCCapacityPolicy(
    cache_size=args.remote_size,
    block_size=args.cache_line_size,
    num_sets=remote_sets,
    partition_ids=list(range(args.num_cores)),
    allocations=initial_allocation,
)
partition_manager = UACCPartitionManager(
    capacity_policy=capacity_policy,
    partitioning_policies=[capacity_policy],
)
system.uacc_controller = UACCController(
    num_cores=args.num_cores,
    cache_line_size=args.cache_line_size,
    profile_interval=args.profile_interval,
    sampled_sets=args.sampled_sets,
    profile_depth=args.profile_depth,
    base_ways=0,
    max_remote_ways=args.max_remote_ways,
    distance_ns=[args.distance_ns] * args.num_cores,
    d2d_rtt=f"{d2d_rtt_ns:g}ns",
    # The paper reports decimal GB/s, while gem5's MemoryBandwidth parser
    # treats the G prefix as GiB.  Pass an explicit B/s value so the
    # controller receives the exact decimal bandwidth in ticks/byte.
    d2d_bandwidth=f"{d2d_bandwidth_gb_s * 1_000_000_000:g}B/s",
    request_size=request_size,
    allocation_policy=args.uacc_policy,
    queue_model=args.uacc_queue_model,
    min_arrival_samples=args.min_arrival_samples,
    ca2_ewma_shift=2,
    feedback_ewma_shift=2,
    feedback_beta_max=args.feedback_beta_max,
    rho_max=args.rho_max,
    queue_occupancy_threshold=args.queue_occupancy_threshold,
    backpressure_threshold=args.backpressure_threshold,
    contraction_windows=args.contraction_windows,
    initial_allocation=initial_allocation,
    dynamic_allocation=args.uacc_policy != "static",
    partition_manager=partition_manager,
)

system.uacc_xbar = NoncoherentXBar(
    width=16, frontend_latency=1, forward_latency=1, response_latency=1
)
system.uacc_cache = UACCCache(
    size=args.remote_size,
    assoc=args.remote_assoc,
    tag_latency=1,
    data_latency=1,
    response_latency=1,
    mshrs=8,
    tgts_per_mshr=8,
    write_buffers=4,
    replacement_policy=LRURP(),
    partitioning_manager=partition_manager,
    controller=system.uacc_controller,
)
system.uacc_cache.cpu_side = system.uacc_xbar.mem_side_ports
system.uacc_cache.mem_side = system.membus.cpu_side_ports

traffic_generators = []
for core_id in range(args.num_cores):
    tgen = PyTrafficGen(
        max_outstanding_reqs=args.max_outstanding,
        elastic_req=True,
    )
    selector = UACCSelector(
        core_id=core_id,
        cache_line_size=args.cache_line_size,
        local_size=args.local_size,
        local_assoc=args.local_assoc,
        atomic_swap=args.atomic_swap,
        controller=system.uacc_controller,
    )
    link = SerialLink(
        # SerialLink models one-way delay. Table 3 reports RTT, so use half
        # the selected RTT in each direction. At the 1 GHz link clock,
        # eight lanes at N Gb/s represent 8*N Gb/s = N GB/s.
        delay=f"{d2d_rtt_ns / 2.0:g}ns",
        req_size=d2d_buffer_flits,
        resp_size=d2d_buffer_flits,
        num_lanes=8,
        link_speed=max(1, int(round(d2d_bandwidth_gb_s))),
    )

    setattr(system, f"tgen{core_id}", tgen)
    setattr(system, f"uacc_selector{core_id}", selector)
    setattr(system, f"uacc_link{core_id}", link)
    traffic_generators.append((tgen, core_id))

    tgen.port = selector.cpu_side_port
    selector.mem_side_port = system.membus.cpu_side_ports
    selector.remote_side_port = link.cpu_side_port
    link.mem_side_port = system.uacc_xbar.cpu_side_ports

system.system_port = system.membus.cpu_side_ports
print(
    "UACC D2D: "
    f"interface={args.d2d_interface} point={args.d2d_point} "
    f"rtt={d2d_rtt_ns:g}ns bandwidth={d2d_bandwidth_gb_s:g}GB/s "
    f"buffer={d2d_buffer_flits} flits model={args.uacc_queue_model}"
)
root = Root(full_system=False, system=system)
m5.instantiate()

for tgen, core_id in traffic_generators:
    base = core_id * working_set_bytes
    passes = [
        tgen.createLinear(
            args.phase_ticks,
            base,
            base + working_set_bytes,
            request_size,
            args.period,
            args.period,
            args.read_percent,
            working_set_bytes,
        )
        for _ in range(args.passes)
    ]
    tgen.start(passes + [tgen.createExit(1)])

exit_event = m5.simulate(args.maxtick)
print(f"Exiting @ tick {m5.curTick()} because {exit_event.getCause()}")
