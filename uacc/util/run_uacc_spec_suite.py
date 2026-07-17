#!/usr/bin/env python3

"""Build and run bounded SPEC trace-replay experiments for UACC.

This suite uses DPC3 ChampSim traces to exercise long-running UACC control
behavior.  It is a memory-system mechanism test, not a full SPEC performance
run: instruction timing and access sizes are synthesized by the converter.
"""

import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]

WORKLOADS = {
    "mcf": "605.mcf_s-994B.champsimtrace.xz",
    "omnetpp": "620.omnetpp_s-141B.champsimtrace.xz",
    "xalancbmk": "623.xalancbmk_s-202B.champsimtrace.xz",
    "fotonik3d": "649.fotonik3d_s-1176B.champsimtrace.xz",
    "gcc": "602.gcc_s-1850B.champsimtrace.xz",
    "x264": "625.x264_s-20B.champsimtrace.xz",
    "leela": "641.leela_s-334B.champsimtrace.xz",
    "exchange2": "648.exchange2_s-584B.champsimtrace.xz",
}

SCENARIOS = {
    "single_memory": ["mcf"],
    "single_bursty": ["omnetpp"],
    "memory_mix4": ["mcf", "omnetpp", "xalancbmk", "fotonik3d"],
    "balanced_mix4": ["gcc", "x264", "leela", "exchange2"],
}


def memory_size(value):
    suffixes = {
        "B": 1,
        "KiB": 1024,
        "MiB": 1024**2,
        "GiB": 1024**3,
    }
    for suffix, multiplier in sorted(
        suffixes.items(), key=lambda item: len(item[0]), reverse=True
    ):
        if value.endswith(suffix):
            return int(value[: -len(suffix)], 0) * multiplier
    return int(value, 0)


def parse_args():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--scenario",
        action="append",
        choices=sorted(SCENARIOS),
        help="scenario to run; may be repeated",
    )
    parser.add_argument(
        "--trace-root",
        type=Path,
        default=Path.home() / "ChampSim/dpc3/spec2k17",
    )
    parser.add_argument("--output-dir", type=Path,
                        default=Path("/tmp/uacc-spec-suite"))
    parser.add_argument("--gem5", type=Path,
                        default=Path("build/X86/gem5.opt"))
    parser.add_argument("--instructions", type=int, default=1_000_000)
    parser.add_argument("--skip-instructions", type=int, default=0)
    parser.add_argument("--ticks-per-instruction", type=int, default=1000)
    parser.add_argument("--request-size", type=int, default=8)
    parser.add_argument("--region-size", type=memory_size,
                        default=64 * 1024 * 1024)
    parser.add_argument("--remote-size", default="1MiB")
    parser.add_argument("--remote-assoc", type=int, default=16)
    parser.add_argument("--max-remote-ways", type=int, default=16)
    parser.add_argument("--profile-interval", type=int, default=10_000)
    parser.add_argument("--stats-windows", type=int, default=10)
    parser.add_argument("--min-arrival-samples", type=int, default=32)
    parser.add_argument(
        "--queue-model",
        choices=["gg1-feedback", "section14"],
        default="gg1-feedback",
    )
    parser.add_argument("--d2d-interface", default="UCIe")
    parser.add_argument("--d2d-rtt-ns", type=float)
    parser.add_argument("--d2d-bandwidth-gb-s", type=float)
    parser.add_argument("--d2d-buffer-flits", type=int)
    parser.add_argument("--queue-occupancy-threshold", type=int, default=0)
    parser.add_argument("--backpressure-threshold", type=int, default=0)
    parser.add_argument("--contraction-windows", type=int, default=3)
    parser.add_argument("--recovery-windows", type=int, default=3)
    parser.add_argument("--max-outstanding", type=int, default=64)
    parser.add_argument("--force-convert", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def stat_sections(stats_path):
    sections = []
    current = None
    for line in stats_path.read_text().splitlines():
        if line.startswith("---------- Begin Simulation Statistics"):
            current = {}
        elif line.startswith("---------- End Simulation Statistics"):
            if current is not None:
                sections.append(current)
            current = None
        elif current is not None:
            fields = line.split()
            if len(fields) >= 2:
                if "|" in fields[1:]:
                    values = []
                    for field in fields[1:]:
                        if field == "#":
                            break
                        if field == "|":
                            continue
                        try:
                            values.append(float(field))
                        except ValueError:
                            break
                    current[f"{fields[0]}::__vector__"] = values
                    continue
                try:
                    current[fields[0]] = float(fields[1])
                except ValueError:
                    pass
    return sections


def vector(section, name, count, default=0.0):
    values = section.get(f"{name}::__vector__")
    if values is not None:
        return (values + [default] * count)[:count]
    if count == 1 and name in section:
        return [section[name]]
    return [section.get(f"{name}::{index}", default)
            for index in range(count)]


def write_summary(run_dir, scenario, workloads, instructions, command):
    sections = stat_sections(run_dir / "stats.txt")
    rows = []
    for section in sections:
        cores = len(workloads)
        allocation = vector(
            section, "system.uacc_controller.allocationWays", cores
        )
        request_indices = [core * 2 for core in range(cores)]
        request_values = lambda stat: [
            section.get(f"system.uacc_controller.{stat}::{index}", 0.0)
            for index in request_indices
        ]
        rows.append({
            "tick": int(section.get("simTicks", 0)),
            "profile_windows": int(section.get(
                "system.uacc_controller.profileWindows", 0
            )),
            "allocation_changes": int(section.get(
                "system.uacc_controller.allocationChanges", 0
            )),
            "allocation": [int(value) for value in allocation],
            "remote_lookups": int(section.get(
                "system.uacc_controller.remoteLookups", 0
            )),
            "remote_hits": int(section.get(
                "system.uacc_controller.remoteHits", 0
            )),
            "max_request_utilization": max(request_values(
                "queueUtilization"
            ), default=0.0),
            "max_request_wait": max(request_values(
                "queueObservedWait"
            ), default=0.0),
            "max_request_feedback": max(request_values(
                "queueFeedback"
            ), default=1.0),
            "request_backpressure": sum(request_values(
                "queueBackpressure"
            )),
            "max_request_occupancy": max(request_values(
                "queueOccupancy"
            ), default=0.0),
        })

    with (run_dir / "timeline.csv").open("w", newline="") as output:
        fieldnames = list(rows[0]) if rows else ["tick"]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            encoded = dict(row)
            encoded["allocation"] = ";".join(
                str(value) for value in row["allocation"]
            )
            writer.writerow(encoded)

    summary = {
        "scenario": scenario,
        "workloads": workloads,
        "instructions_per_core": instructions,
        "command": command,
        "timeline": rows,
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    return rows


def main():
    args = parse_args()
    scenarios = args.scenario or ["memory_mix4"]
    if args.instructions <= 0 or args.ticks_per_instruction <= 0:
        raise SystemExit("instruction count and timing must be positive")
    if args.profile_interval <= 0 or args.stats_windows <= 0:
        raise SystemExit("profiling and stats intervals must be positive")
    if args.max_remote_ways > args.remote_assoc:
        raise SystemExit("max remote ways cannot exceed remote associativity")

    gem5 = args.gem5 if args.gem5.is_absolute() else REPO_ROOT / args.gem5
    if not gem5.is_file() and not args.dry_run:
        raise SystemExit(f"gem5 binary does not exist: {gem5}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    converted_dir = args.output_dir / "traces"
    converted_dir.mkdir(exist_ok=True)

    for scenario in scenarios:
        workloads = SCENARIOS[scenario]
        trace_files = []
        for workload in workloads:
            source = args.trace_root / WORKLOADS[workload]
            if not source.is_file():
                raise SystemExit(f"missing source trace: {source}")
            converted = converted_dir / (
                f"{workload}-{args.skip_instructions}-"
                f"{args.instructions}.trc.gz"
            )
            if args.force_convert or not converted.is_file():
                conversion = [
                    sys.executable,
                    str(REPO_ROOT / "uacc/util/champsim_trace_to_packet.py"),
                    str(source),
                    str(converted),
                    "--max-instructions", str(args.instructions),
                    "--skip-instructions", str(args.skip_instructions),
                    "--ticks-per-instruction",
                    str(args.ticks_per_instruction),
                    "--memory-size", str(args.region_size),
                    "--request-size", str(args.request_size),
                ]
                print("CONVERT", " ".join(conversion), flush=True)
                if not args.dry_run:
                    subprocess.run(conversion, cwd=REPO_ROOT, check=True)
            trace_files.append(converted)

        cores = len(workloads)
        clock_ticks = 1000
        stats_interval = (
            args.profile_interval * clock_ticks * args.stats_windows
        )
        max_tick = (
            args.instructions * args.ticks_per_instruction +
            2 * args.profile_interval * clock_ticks
        )
        run_dir = args.output_dir / f"{scenario}-{args.queue_model}"
        run_dir.mkdir(parents=True, exist_ok=True)
        command = [
            str(gem5), "-d", str(run_dir),
            "configs/example/uacc.py",
            "--num-cores", str(cores),
            "--trace-addr-stride", f"{args.region_size}B",
            "--memory-size", f"{args.region_size * cores}B",
            "--request-size", str(args.request_size),
            "--uacc-policy", "congestion",
            "--uacc-queue-model", args.queue_model,
            "--initial-allocation", "0",
            "--max-remote-ways", str(args.max_remote_ways),
            "--remote-size", args.remote_size,
            "--remote-assoc", str(args.remote_assoc),
            "--profile-interval", str(args.profile_interval),
            "--min-arrival-samples", str(args.min_arrival_samples),
            "--stats-interval", str(stats_interval),
            "--maxtick", str(max_tick),
            "--d2d-interface", args.d2d_interface,
            "--queue-occupancy-threshold",
            str(args.queue_occupancy_threshold),
            "--backpressure-threshold", str(args.backpressure_threshold),
            "--contraction-windows", str(args.contraction_windows),
            "--recovery-windows", str(args.recovery_windows),
            "--max-outstanding", str(args.max_outstanding),
        ]
        for trace_file in trace_files:
            command.extend(["--trace-file", str(trace_file.resolve())])
        for option, value in (
            ("--d2d-rtt-ns", args.d2d_rtt_ns),
            ("--d2d-bandwidth-gb-s", args.d2d_bandwidth_gb_s),
            ("--d2d-buffer-flits", args.d2d_buffer_flits),
        ):
            if value is not None:
                command.extend([option, str(value)])

        (run_dir / "command.txt").write_text(" ".join(command) + "\n")
        print("RUN", " ".join(command), flush=True)
        if args.dry_run:
            continue
        with (run_dir / "run.log").open("w") as log:
            subprocess.run(
                command, cwd=REPO_ROOT, stdout=log,
                stderr=subprocess.STDOUT, check=True
            )
        rows = write_summary(
            run_dir, scenario, workloads, args.instructions, command
        )
        final = rows[-1] if rows else {}
        print(
            f"DONE scenario={scenario} allocation={final.get('allocation')} "
            f"changes={final.get('allocation_changes')} "
            f"lookups={final.get('remote_lookups')} "
            f"hits={final.get('remote_hits')} "
            f"max_wait={final.get('max_request_wait')}",
            flush=True,
        )


if __name__ == "__main__":
    main()
