# UACC Section 14 Q4.4 range-width sweep

This experiment fixes CA2/beta and cost precision at Q4.4, keeps the
utilization threshold at independent Q1.8 precision, and keeps the implemented
R16 reciprocal. The scan changes only the supported integer range.

## Range assumptions

The coherent width profiles are derived from the following bounds rather than
from the observed maxima of one trace:

| Assumption | Value |
|---|---:|
| Maximum packets per domain | `Tmax` |
| Queueing domains | 8 |
| Maximum service time | 32 cycles |
| Maximum observed wait per packet | 4096 cycles |
| Maximum fixed latency | 512 cycles |
| Maximum capacity gain per packet | 512 cycles |
| CA2 maximum | 8 |
| Beta maximum | 4 |
| Utilization limit | 0.9, represented independently as Q1.8 |

The arrival-sum derivation assumes that the first inter-arrival gap after an
idle window is clamped to at most one profiling window. The current collector
does not yet implement that clamp, so it must be added and differentially
tested before applying the reduced collector widths in RTL.

## Derived widths

The four synthesized implementations have the following meanings. `W1M` is
still measured with the same 100K-cycle test workload; its name describes the
range the hardware can support, not the duration used for that PPA run.

| Implementation | Meaning | Intended use |
|---|---|---|
| Lossy16 | Deliberately unsafe 100K failure point. Count/window remain 17 bits and the divider remains 51 bits, but R1/R2 and the queue-cost-to-score path are aggressively narrowed. The name does not mean that every signal is 16 bits. | Quantify the area saving and decision failures beyond the safe boundary; not deployable. |
| W100K | Coherent minimum-range implementation derived for at most 100,000 cycles and 100,000 packets per domain. | Recommended implementation for the current 100K profiling window. |
| W1M | Coherent conservative implementation derived for at most 1,000,000 cycles and packets per domain. | Measure the cost of 10x operating-range headroom. |
| Legacy wide | Original prototype widths, including 32/40-bit count/window, 56/80-bit R1/R2, and a 128-bit shared divider. It keeps Q4.4/Q1.8/R16 for this controlled comparison. | Over-provisioned control point; not a different algorithm. |

The primary independent variable is the **integer dynamic range**, represented
by the following width groups. Fractional precision does not change in this
experiment.

| Width group | Function | Why it affects implementation cost |
|---|---|---|
| Count/window | Packet count `R0` and profiling-window limit | Sets the base accumulator and interface widths. |
| R1 busy sum | Sum of `count * service_cycles`; also used in the utilization guard and `R1^2` term | Changes multipliers, comparators, and the dominant queue-cost numerator path. |
| R2 service-second sum | Sum of `count * service_square` | Changes the `R0 * R2` multiplier and queue-cost numerator. |
| Cost integer | Per-domain model and beta-adjusted queue cost | Changes the reciprocal-cost core and per-domain result width. |
| Total cost / signed score | Cross-domain queue/fixed-cost accumulation, MU subtraction, lookahead score, and best-candidate comparison | Changes wide add/subtract, registers, muxes, and comparators across the candidate loop. |
| Shared divider | Rolled CA2 and feedback-beta numerator datapath | Affects divider register/mux width and worst-case cycles; it is 51/61/128 bits for W100K/W1M/Legacy. |

Arrival statistics, wait sums, service values, and fixed-latency fields also
follow the selected coherent range profile, as listed below. They are secondary
variables rather than the intended axis. The allocator PPA includes its input
ports and arithmetic using these types, but the projected controller always
adds the same existing collector. Reduced collector storage is not synthesized
in this comparison.

| Signal or datapath | W10K | W50K | Lossy16 | W100K | W200K | W1M | Legacy wide |
|---|---:|---:|---:|---:|---:|---:|---:|
| Packet count | 14 | 16 | 17 | 17 | 18 | 20 | 32 |
| Window cycles | 14 | 16 | 17 | 17 | 18 | 20 | 40 |
| Inter-arrival sum | 15 | 17 | 18 | 18 | 19 | 21 | 64 |
| Inter-arrival square sum | 28 | 33 | 35 | 35 | 37 | 41 | 96 |
| Observed-wait sum | 26 | 28 | 29 | 29 | 30 | 32 | 64 |
| Service cycles | 6 | 6 | 6 | 6 | 6 | 6 | 24 |
| Service square | 11 | 11 | 11 | 11 | 11 | 11 | 48 |
| R1 busy sum | 19 | 21 | 16 | 22 | 23 | 25 | 56 |
| R2 service-second sum | 24 | 26 | 20 | 27 | 28 | 30 | 80 |
| Per-domain adjusted queue cost, integer | 28 | 30 | 20 | 31 | 32 | 35 | 80 |
| Total cost, integer | 31 | 33 | 23 | 34 | 35 | 38 | 84 |
| Signed score, including sign | 32 | 34 | 24 | 35 | 36 | 39 | 85 |
| Shared-divider integer datapath | 41 | 48 | 51 | 51 | 54 | 61 | 128 |

Each Q4.4 value has four additional fractional bits where applicable.
Lossy16 deliberately violates the R1/R2 and cost range proofs while retaining
the 100K count, window, and shared-divider widths. It is included as a measured
failure point, not as a supported 100K configuration. Overflow follows the HLS
saturation/invalid-candidate path and never wraps silently.

## Software pre-simulation

The pre-simulation uses 10,000 deterministic seeds, a 100,000-cycle window,
on/off plus Poisson traffic, integer packet-class counts and integer service
cycles. It models the HLS Q16 reciprocal LUT plus one Newton step, truncation,
independent Q1.8 utilization threshold, and stage saturation.

| Width profile | Agreement | Mismatches | Saturations | Objective MAE | P99 error | Max error | Max regret |
|---|---:|---:|---:|---:|---:|---:|---:|
| W10K | 0% | 10,000 | 130,000 | N/A | N/A | N/A | 143,114 cycles |
| W50K | 0% | 10,000 | 100,000 | 192,562 | 573,374 | 756,263 | 143,114 cycles |
| W100K | 100% | 0 | 0 | 1,925.75 | 12,132.3 | 15,234.8 | 0 |
| W200K | 100% | 0 | 0 | 1,925.75 | 12,132.3 | 15,234.8 | 0 |
| W1M | 100% | 0 | 0 | 1,925.75 | 12,132.3 | 15,234.8 | 0 |
| Legacy wide | 100% | 0 | 0 | 1,925.75 | 12,132.3 | 15,234.8 | 0 |

W10K and W50K are intentionally evaluated against a 100K window here. Their
failures are range violations, not evidence that their arithmetic is wrong at
their intended window length. The current rolled allocator also has a roughly
61K-cycle HLS worst latency, so neither is a viable implementation target for
the present architecture.

The nonzero objective error is the common Q4.4/R16 arithmetic error relative
to the ideal reference. It is identical for every range-safe profile and does
not come from integer-width reduction. It causes no allocation disagreement or
oracle regret in these 10,000 seeds.

Observed synthetic maxima were 20,546 packets, 144,988 busy cycles,
2,282,520 service-second cycles, 410,920 fixed-cost cycles, and 1,363,680 gain
cycles. These observations are reported as diagnostics only; the proposed
widths continue to use the stated theoretical bounds.

## Engineering accuracy and PPA trade-off

A second 10,000-seed scan uses a broader deterministic stress distribution at
the same 100K-cycle window. Traffic rate, on/off burst length, Poisson load, and
link bandwidth all vary by seed. The oracle column compares each fixed-point
implementation with the ideal reference; the W100K column isolates additional
errors caused by range reduction from the common Q4.4/R16 approximation.

| Profile | Count/window bits | R1/R2 bits | Cost/total/score integer bits | Oracle agreement | Agreement vs W100K | Saturations | Allocator area | Area vs W100K | Allocator Fmax | Allocator default power | Projected controller area |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Lossy16 | 17/17 | 16/20 | 20/23/24 | 74.410% | 74.170% | 81,120 | 4,326.72 um^2 | -16.97% | 467.940 MHz | 15.126 mW | 0.008460 mm^2 |
| W100K | 17/17 | 22/27 | 31/34/35 | 99.410% | 100.000% | 0 | 5,210.88 um^2 | baseline | 474.548 MHz | 11.689 mW | 0.009344 mm^2 |
| W1M | 20/20 | 25/30 | 35/38/39 | 99.410% | 100.000% | 0 | 5,952.87 um^2 | +14.24% | 476.146 MHz | 12.868 mW | 0.010086 mm^2 |
| Legacy wide | 32/40 | 56/80 | 80/84/85 | 99.410% | 100.000% | 0 | 18,462.10 um^2 | +254.30% | 416.705 MHz | 154.537 mW | 0.022596 mm^2 |

Lossy16 disagrees with the oracle in 2,559 of 10,000 windows and with W100K in
2,583 windows. It records 47,670 R1 and 33,450 R2 saturation events. By
contrast, W100K, W1M, and legacy wide are bit-for-bit decision-equivalent to
one another and have no range saturation. Their 59 common oracle mismatches
come from the fixed Q4.4/R16 approximation under this harder workload, not from
integer range.

The first Lossy16 failure is seed 1. W100K and the oracle choose candidate 7,
allocation `[2, 0]`, while Lossy16 falls back to candidate 1, allocation
`[0, 0]`. Candidate 7 has R1=65,688, just above the 16-bit maximum 65,535, and
R2=1,755,488, above the 20-bit maximum 1,048,575. The protected implementation
therefore invalidates it; it does not wrap. The resulting oracle regret is
175,447.575 cycles/window. This concrete failure shows why the 16.97% allocator
area saving, or 9.46% projected controller saving, is not an acceptable trade.

## HLS implementation results

The controlled implementation uses four Q4.4/Q1.8/R16 points. All variants
passed C simulation, their complete C/Verilog cosimulation suites (266
transactions for Lossy16 and 265 for each other point), and independent Icarus
compilation of the generated Verilog.

| Width profile | DSP | FF | LUT | Estimated period | HLS worst latency | Observed RTL latency | Shared-divider latency |
|---|---:|---:|---:|---:|---:|---:|---:|
| Lossy16 | 19 | 5,390 | 5,130 | 4.254 ns | 29,469 cycles | 576--779 cycles | 167 cycles |
| W100K | 26 | 6,773 | 6,184 | 4.576 ns | 29,898 cycles | 671--852 cycles | 167 cycles |
| W1M | 29 | 6,867 | 6,172 | 4.710 ns | 33,557 cycles | 672--883 cycles | 197 cycles |
| Legacy wide | 46 | 17,714 | 10,573 | 3.853 ns | 60,986 cycles | 1,131--1,542 cycles | 398 cycles |

Relative to the legacy-wide control, W100K reduces the HLS estimates by 43.5%
DSP, 61.8% FF, and 41.5% LUT. Its worst-case compute estimate leaves 70,102
cycles, or 70.1%, of a 100K-cycle profiling window unused. This is a genuine
integer-range reduction: fractional precision, reciprocal precision, semantic
input/output arrays, candidate count, and diagnostic result set are unchanged.
Lossy16 reduces the HLS estimates by a further 26.9% DSP, 20.4% FF, and 17.0%
LUT relative to W100K, but these savings must be evaluated against its measured
decision failures above.

## ASAP7 allocator PPA

All four allocator runs use ASAP7 RVT at 0.77 V, the same 5 ns SDC, 25% core
utilization, 0.40 placement density, CTS, and global routing. Detailed routing
is skipped, so timing and power use global-route parasitic estimates.

| Width profile | Cell area | Std cells | Die area | I/O bits | Setup slack | Hold slack | Fmax |
|---|---:|---:|---:|---:|---:|---:|---:|
| Lossy16 | 4,326.72 um^2 | 31,958 | 19,337.7 um^2 | 894 | +2,862.97 ps | +18.429 ps | 467.940 MHz |
| W100K | 5,210.88 um^2 | 38,246 | 23,142.0 um^2 | 826 | +2,892.73 ps | +11.583 ps | 474.548 MHz |
| W1M | 5,952.87 um^2 | 43,725 | 26,314.7 um^2 | 934 | +2,899.81 ps | +13.923 ps | 476.146 MHz |
| Legacy wide | 18,462.10 um^2 | 135,285 | 78,181.2 um^2 | 1,272 | +2,600.22 ps | +0.209 ps | 416.705 MHz |

All points have zero setup TNS, zero hold TNS, and zero flow errors. W100K
reduces allocator standard-cell area by 71.78% relative to legacy wide. W1M is
14.24% larger than W100K, showing that the first reduction from oversized
40--128-bit datapaths matters far more than the difference between coherent
100K and 1M operating envelopes.
Lossy16 is 16.97% smaller than W100K at allocator level. Its larger I/O count
despite narrower arithmetic is another HLS packing/scheduling effect rather
than a monotonic function of source-level bit width.

| Width profile | Internal | Switching | Leakage | Total power |
|---|---:|---:|---:|---:|
| Lossy16 | 9.848 mW | 5.274 mW | 0.0032 mW | 15.126 mW |
| W100K | 7.733 mW | 3.953 mW | 0.0038 mW | 11.689 mW |
| W1M | 8.601 mW | 4.263 mW | 0.0044 mW | 12.868 mW |
| Legacy wide | 92.490 mW | 62.034 mW | 0.0141 mW | 154.537 mW |

The apparent 92.4% W100K power reduction is a default-activity flow estimate,
not a workload-average result. It reflects the much smaller clocked and
combinational implementation, but the magnitude must be confirmed with one
common trace-derived VCD/SAIF before it can support a power claim.
Lossy16 is smaller but has 29.4% higher default-activity power than W100K.
This non-monotonic result reflects changed HLS scheduling, port packing, and
the flow's assumed activity; it is not evidence of higher workload energy.

## Combined controller projection

For continuity with the previous report, the following projection adds the
unchanged detailed-route collector: 4,133.5 um^2, 4.533 mW default-activity
power, and 456.775 MHz Fmax. The allocator already contains the direct-cost
core, so it is not counted again.

| Width profile | Controller area | Controller Fmax | Default-activity power |
|---|---:|---:|---:|
| Lossy16 | 8,460.22 um^2 (0.008460 mm^2) | 456.775 MHz | 19.659 mW |
| W100K | 9,344.38 um^2 (0.009344 mm^2) | 456.775 MHz | 16.223 mW |
| W1M | 10,086.37 um^2 (0.010086 mm^2) | 456.775 MHz | 17.401 mW |
| Legacy wide | 22,595.60 um^2 (0.022596 mm^2) | 416.705 MHz | 159.070 mW |

W100K reduces this projected controller area by 58.65% versus the controlled
legacy-wide point. The collector becomes the projected frequency limiter, so
the allocator retains substantial margin at the 200 MHz design target.
Lossy16 reduces projected controller area by only 9.46% relative to W100K
because the unchanged collector is now almost half of the total.

## Interpretation and limitations

- The sweep keeps the same semantic arrays and all diagnostic outputs, but
  Vitis HLS changes physical memory-port packing with element width. This is
  visible in the 894/826/934/1,272 I/O-bit counts; W1M also receives a second
  `candidate_valid` memory port. The results therefore measure deployable HLS
  implementations, not an internal-datapath-only experiment behind a fixed
  bus shell.
- HLS pads some narrow memory data ports to 8/16/32/64 bits. Unused padded
  inputs are reported as dangling and are optimized away. A production wrapper
  and memory-macro mapping are still needed for final interface and SRAM area.
- The reduced collector widths are not enabled. The arrival-sum proof assumes
  a one-window clamp for the first gap after an idle interval; the collector
  must implement and differentially test that clamp first.
- The current collector also still needs its packet-class storage behavior and
  event-ingress throughput resolved before the combined projection can be
  treated as a complete controller result.
- The accuracy run is synthetic rather than gem5 trace replay. ATD logic,
  configuration/snapshot memories, event FIFO, and top-level integration are
  outside these HLS tops.
- Allocator timing and power are post-global-route estimates, not detailed-route
  or signoff values. Power uses default activity, not workload VCD/SAIF.

Raw software results are in
`uacc/hw/range_width_sweep_q4_10000.json`; the engineering implementation
comparison is in `uacc/hw/range_width_accuracy_q4_10000.json`. The runners are
`uacc/hw/range_precision_sweep.py`,
`uacc/hw/hls/run_width_sweep.sh`, and
`uacc/hw/openroad/run_width_sweep.sh`; archived physical reports are under
`uacc/hw/openroad/asap7/width_sweep_results/`.
