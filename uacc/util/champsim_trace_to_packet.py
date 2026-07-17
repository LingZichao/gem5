#!/usr/bin/env python3

"""Convert a bounded ChampSim instruction-trace slice to a gem5 packet trace.

The converter keeps data-memory references only.  Since the ChampSim format
does not carry access sizes or timestamps, accesses use a configurable size
and instructions advance at a configurable interval.  Addresses are mapped
at cache-line granularity into a bounded memory region while preserving the
trace's temporal line-reuse pattern.
"""

import argparse
import gzip
import importlib.util
import lzma
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile


os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")


UTIL_DIR = Path(__file__).resolve().parent
REPO_ROOT = UTIL_DIR.parents[1]
sys.path.insert(0, str(UTIL_DIR))
import protolib  # noqa: E402


# DPC3 traces use ChampSim's input_instr from inc/trace_instruction.h.
CHAMPSIM_INSTR = struct.Struct("<QBB2B4B2Q4Q")


def load_packet_proto():
    temporary = tempfile.TemporaryDirectory(prefix="gem5-packet-proto-")
    subprocess.run(
        [
            "protoc",
            f"--python_out={temporary.name}",
            f"--proto_path={REPO_ROOT / 'src/proto'}",
            str(REPO_ROOT / "src/proto/packet.proto"),
        ],
        check=True,
    )
    module_path = Path(temporary.name) / "packet_pb2.py"
    spec = importlib.util.spec_from_file_location("packet_pb2", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, temporary


def parse_args():
    parser = argparse.ArgumentParser(
        description="Convert a ChampSim SPEC trace slice for gem5 TrafficGen"
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--max-instructions", type=int, default=1_000_000)
    parser.add_argument("--skip-instructions", type=int, default=0)
    parser.add_argument("--ticks-per-instruction", type=int, default=1000)
    parser.add_argument("--memory-size", type=lambda x: int(x, 0),
                        default=256 * 1024 * 1024)
    parser.add_argument("--line-size", type=int, default=64)
    parser.add_argument("--request-size", type=int, default=8)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.max_instructions <= 0 or args.skip_instructions < 0:
        raise SystemExit("instruction counts must be non-negative")
    if args.ticks_per_instruction <= 0:
        raise SystemExit("--ticks-per-instruction must be positive")
    if args.line_size <= 0 or args.memory_size < args.line_size:
        raise SystemExit("memory and line sizes are invalid")
    if not 0 < args.request_size <= args.line_size:
        raise SystemExit("--request-size must fit in one cache line")

    packet_pb2, temporary = load_packet_proto()
    del temporary  # Keep the generated module loaded; files are no longer needed.

    args.output.parent.mkdir(parents=True, exist_ok=True)
    output_open = gzip.open if args.output.suffix == ".gz" else open
    line_count = args.memory_size // args.line_size
    instruction = 0
    packets = 0
    reads = 0
    writes = 0

    with lzma.open(args.input, "rb") as trace, output_open(
        args.output, "wb"
    ) as output:
        output.write(b"gem5")
        header = packet_pb2.PacketHeader()
        header.obj_id = f"ChampSim slice from {args.input.name}"
        header.tick_freq = 1_000_000_000_000
        protolib.encodeMessage(output, header)

        while instruction < args.skip_instructions:
            if len(trace.read(CHAMPSIM_INSTR.size)) != CHAMPSIM_INSTR.size:
                raise SystemExit("trace ended while skipping instructions")
            instruction += 1

        converted = 0
        while converted < args.max_instructions:
            raw = trace.read(CHAMPSIM_INSTR.size)
            if not raw:
                break
            if len(raw) != CHAMPSIM_INSTR.size:
                raise SystemExit("truncated ChampSim instruction record")
            fields = CHAMPSIM_INSTR.unpack(raw)
            ip = fields[0]
            destination_memory = fields[9:11]
            source_memory = fields[11:15]
            # Avoid issuing at tick zero. BaseTrafficGen uses zero as the
            # sentinel for "no retry packet", so a tick-zero request that is
            # backpressured by another core cannot be retried safely.
            tick = (converted + 1) * args.ticks_per_instruction

            for command, addresses in ((1, source_memory),
                                       (4, destination_memory)):
                for address in addresses:
                    if address == 0:
                        continue
                    mapped = ((address // args.line_size) % line_count) * \
                        args.line_size
                    packet = packet_pb2.Packet()
                    packet.tick = tick
                    packet.cmd = command
                    packet.addr = mapped
                    packet.size = args.request_size
                    packet.pc = ip
                    protolib.encodeMessage(output, packet)
                    packets += 1
                    reads += command == 1
                    writes += command == 4

            instruction += 1
            converted += 1

    print(
        f"converted_instructions={converted} packets={packets} "
        f"reads={reads} writes={writes} output={args.output}"
    )


if __name__ == "__main__":
    main()
