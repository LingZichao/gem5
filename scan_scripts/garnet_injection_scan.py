#!/usr/bin/env python3
"""
Utility to sweep Garnet synthetic traffic injection rates and plot the results.

Example usage:

    python3 util/garnet_injection_scan.py \
        --gem5-binary build/Garnet_standalone/gem5.debug \
        --config configs/example/garnet_synth_traffic.py \
        --common-args "--num-cpus=16 --num-dirs=16 --network=garnet \
                       --topology=Mesh_XY --mesh-rows=4 --sim-cycles=1000 \
                       --router-latency=4 --synthetic=uniform_random \
                       --inj-vnet=2 --routing-algorithm=0"
"""

from __future__ import annotations

import argparse
import csv
import shlex
import subprocess
from dataclasses import dataclass
from decimal import (
    Decimal,
    getcontext,
)
from pathlib import Path
from typing import (
    Dict,
    Iterable,
    List,
    Sequence,
)

try:
    import matplotlib.pyplot as plt  # type: ignore
except Exception:  # pragma: no cover - matplotlib is optional
    plt = None


STAT_KEYS = {
    "packets_injected": "system.ruby.network.packets_injected::total",
    "packets_received": "system.ruby.network.packets_received::total",
    "avg_packet_latency": "system.ruby.network.average_packet_latency",
    "avg_queue_latency": "system.ruby.network.average_packet_queueing_latency",
    "avg_network_latency": "system.ruby.network.average_packet_network_latency",
}


@dataclass
class SweepResult:
    rate: float
    packets_injected: float
    packets_received: float
    avg_packet_latency: float
    avg_queue_latency: float
    avg_network_latency: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run gem5 across an injection-rate sweep and plot the data"
    )
    parser.add_argument(
        "--gem5-binary",
        default="build/NULL/gem5.opt",
        help="Path to gem5 binary (default: %(default)s)",
    )
    parser.add_argument(
        "--config",
        default="configs/example/garnet_synth_traffic.py",
        help="Configuration script to use (default: %(default)s)",
    )
    parser.add_argument(
        "--common-args",
        default="",
        help="Quoted string of extra args passed to the configuration script",
    )
    parser.add_argument(
        "--inj-start",
        type=float,
        default=0.1,
        help="Starting injection rate (default: %(default)s)",
    )
    parser.add_argument(
        "--inj-stop",
        type=float,
        default=0.9,
        help="Final injection rate (inclusive, default: %(default)s)",
    )
    parser.add_argument(
        "--inj-step",
        type=float,
        default=0.1,
        help="Injection rate step size (default: %(default)s)",
    )
    parser.add_argument(
        "--output-root",
        default="m5out/injection_sweep",
        help="Directory to store run outputs and plots",
    )
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="Skip a rate if its stats file already exists",
    )
    return parser.parse_args()


def decimal_range(start: float, stop: float, step: float) -> Iterable[float]:
    """Generate Decimal-aware range values to avoid floating-point drift."""
    getcontext().prec = 12
    d_start = Decimal(str(start))
    d_stop = Decimal(str(stop))
    d_step = Decimal(str(step))
    if d_step <= 0:
        raise ValueError("Injection-rate step must be positive")
    cur = d_start
    epsilon = Decimal("1e-12")
    while cur <= d_stop + epsilon:
        yield float(cur)
        cur += d_step


def run_sim(
    gem5_binary: str,
    config: str,
    injection_rate: float,
    run_dir: Path,
    config_args: Sequence[str],
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    cmd: list[str] = [gem5_binary, f"--outdir={run_dir}"]
    cmd.append(config)
    cmd.extend(config_args)
    cmd.append(f"--injectionrate={injection_rate}")
    print(f"[INFO] Running injection rate {injection_rate:.6f}")
    print("       " + " ".join(cmd))
    subprocess.run(cmd, check=True)


def parse_stats(stats_path: Path) -> dict[str, float]:
    values: dict[str, float] = {}
    with stats_path.open() as fh:
        for line in fh:
            stripped = line.strip()
            for field, stat_name in STAT_KEYS.items():
                if stripped.startswith(stat_name):
                    parts = stripped.split()
                    if len(parts) >= 2:
                        values[field] = float(parts[1])
    missing = [field for field in STAT_KEYS if field not in values]
    if missing:
        raise RuntimeError(
            f"Missing fields {missing} in {stats_path}. "
            "Ensure the simulation completed successfully."
        )
    return values


def write_csv(results: Sequence[SweepResult], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(
            [
                "injection_rate",
                "packets_injected",
                "packets_received",
                "avg_packet_latency",
                "avg_network_latency",
                "avg_queue_latency",
            ]
        )
        for result in results:
            writer.writerow(
                [
                    f"{result.rate:.6f}",
                    f"{result.packets_injected:.3f}",
                    f"{result.packets_received:.3f}",
                    f"{result.avg_packet_latency:.3f}",
                    f"{result.avg_network_latency:.3f}",
                    f"{result.avg_queue_latency:.3f}",
                ]
            )
    print(f"[INFO] Wrote CSV results to {csv_path}")


def plot_results(results: Sequence[SweepResult], plot_path: Path) -> None:
    if plt is None:
        print("[WARN] matplotlib not available; skipping plot generation")
        return

    rates = [r.rate for r in results]
    injected = [r.packets_injected for r in results]
    avg_lat = [r.avg_packet_latency for r in results]

    fig, axes = plt.subplots(2, 1, figsize=(7, 6), sharex=True)
    axes[0].plot(rates, injected, marker="o")
    axes[0].set_ylabel("Packets Injected")
    axes[0].grid(True, linestyle="--", alpha=0.4)

    axes[1].plot(rates, avg_lat, marker="o", color="tab:red")
    axes[1].set_xlabel("Injection Rate (packets/cycle/node)")
    axes[1].set_ylabel("Avg Packet Latency (ticks)")
    axes[1].grid(True, linestyle="--", alpha=0.4)

    fig.suptitle("Garnet Injection-Rate Sweep")
    fig.tight_layout()
    plot_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(plot_path)
    print(f"[INFO] Saved plot to {plot_path}")


def main() -> None:
    args = parse_args()
    config_args = shlex.split(args.common_args)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    results: list[SweepResult] = []
    for rate in decimal_range(args.inj_start, args.inj_stop, args.inj_step):
        run_dir = output_root / f"inj_{rate:.6f}"
        stats_path = run_dir / "stats.txt"
        if not (args.keep_existing and stats_path.exists()):
            run_sim(args.gem5_binary, args.config, rate, run_dir, config_args)
        elif args.keep_existing:
            print(f"[INFO] Reusing existing stats at {stats_path}")
        parsed = parse_stats(stats_path)
        results.append(
            SweepResult(
                rate=rate,
                packets_injected=parsed["packets_injected"],
                packets_received=parsed["packets_received"],
                avg_packet_latency=parsed["avg_packet_latency"],
                avg_queue_latency=parsed["avg_queue_latency"],
                avg_network_latency=parsed["avg_network_latency"],
            )
        )

    results.sort(key=lambda r: r.rate)
    csv_path = output_root / "injection_sweep.csv"
    plot_path = output_root / "injection_sweep.png"
    write_csv(results, csv_path)
    plot_results(results, plot_path)


if __name__ == "__main__":
    main()
