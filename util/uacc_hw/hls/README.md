# UACC Section 14 HLS correctness prototype

This directory contains the synthesizable fixed-point implementation of
`model.md` Section 14. It is split into three independently synthesizable HLS
tops so that collection state, queue-cost arithmetic, and the window-boundary
allocator can be verified and measured separately.

## Module boundary

| HLS top | Responsibility |
|---|---|
| `uacc_section14_collector` | Maintain per-domain `N`, `M`, `SA`, `SA2`, class counts, `SW`, occupancy maximum, buffer-full/backpressure counts, and overflow state. |
| `uacc_section14_cost` | Compute the utilization guard, direct `Qmodel`, beta-adjusted queue cost, and explicit validity/saturation status using a Q16 reciprocal approximation. |
| `uacc_section14_allocator` | Update CA2/beta, form `R0/R1/R2`, evaluate queue/fixed costs and capacity gain, apply guards, calculate MU/score, and select the highest positive candidate. |

The allocator input `candidate_class_count` contains the absolute packet-class
counts predicted by the ATD for each candidate. Candidate 0 is the current
allocation. The ATD itself and the final one-way cache-allocation commit remain
system-level blocks; they are not reimplemented in this arithmetic prototype.

## Section 14 coverage

| Section | Formula or behavior | HLS implementation | Verification |
|---|---|---|---|
| 14.1 | Per-domain window state | collector static arrays; saturating updates; snapshot/reset preserves cross-window arrival history | independent domains, class counts, snapshot/reset, clear-all, invalid event, non-monotonic time |
| 14.2 | `CA2=max(0,M*SA2/SA^2-1)` and shift-EWMA | allocator `arrivalCa2` and `shiftEwma` | normal update, `M=0` preservation, `M>0 && SA=0` CA2_MAX |
| 14.3 | Absolute candidate packet counts from ATD, including 0-to-1 activation traffic | `candidate_class_count[candidate][domain][class]` input | activation candidate has request traffic while current candidate has none |
| 14.4 | `R0/R1/R2`, utilization guard, direct queue-cost formula | allocator `trafficSums` plus the single shared `uacc_section14_cost_core` | directed boundaries and 2000 deterministic-random vectors against exact 128-bit division, each within 0.03% |
| 14.5 | measured-wait beta, clipping, shift-EWMA, adjusted candidate cost | allocator `feedbackBeta`; current model is evaluated before candidate costs | normal feedback and beta-max clipping |
| 14.6 | fixed cost, capacity gain, MU, `{1,2,4}` score, guards, highest positive candidate | allocator candidate evaluation and selection | MU signs, best candidate, invalid delta, utilization, occupancy, combined buffer-full/backpressure guard |
| 14.7 | ordered window computation and reset | allocator phase order; collector `SnapshotAndReset` | C/RTL cosimulation of all three tops |

The default external factors and costs use unsigned Q4.8. `1.0` is 256. CA2
and beta are 12-bit Q4.8, utilization is 9-bit Q1.8, and queue cost is 88-bit
Q80.8. `UACC_HLS_FRACTION_BITS` can select another fractional width at compile
time while preserving the integer ranges. `R1` and `R2` remain widened to 56
and 80 bits. The direct-cost numerator is 128 bits, and arithmetic overflow is
reported instead of silently wrapping. The
queue-cost denominator uses a 32-entry Q16 reciprocal LUT over the normalized
range `[1,2)` followed by one Newton correction. The resulting cost remains
within 0.03% of exact truncating division in the deterministic test vectors.
CA2 and beta are clamped by the allocator; the cost core also enforces effective
CA2/beta of at least 1.0. CA2 and beta updates still use exact HLS division.

## Generate and verify Verilog

Run Vitis HLS 2019.2 installed on Windows from WSL:

```sh
util/uacc_hw/hls/run_vitis_hls_from_wsl.sh
```

The wrapper stages sources under `/mnt/e/uacc_hls`, runs synthesis and XSIM
C/Verilog cosimulation, and checks the reports rather than trusting the old
tool's process exit status. To rerun only one top:

```sh
UACC_HLS_START_STAGE=collector util/uacc_hw/hls/run_vitis_hls_from_wsl.sh
UACC_HLS_START_STAGE=allocator util/uacc_hw/hls/run_vitis_hls_from_wsl.sh
```

Generate the controlled F1/F2/F4 allocator variants under
`/mnt/e/uacc_hls/variants/` with:

```sh
util/uacc_hw/hls/run_precision_sweep.sh
```

Generated top modules are:

- `/mnt/e/uacc_hls/uacc_section14_cost/solution1/syn/verilog/uacc_section14_cost_uacc_section14_cost.v`
- `/mnt/e/uacc_hls/uacc_section14_collector/solution1/syn/verilog/uacc_section14_collector_uacc_section14_collector.v`
- `/mnt/e/uacc_hls/uacc_section14_allocator/solution1/syn/verilog/uacc_section14_allocator_uacc_section14_allocator.v`

## Validation on July 15, 2026

| Top | XSIM transactions | Observed RTL latency | xc7z020 estimate | Estimated clock |
|---|---:|---:|---:|---:|
| cost | 2009/2009 PASS | 44 cycles | 0 BRAM, 26 DSP, 4996 FF, 3555 LUT | 3.853 ns |
| collector | 19/19 PASS | 12--36 cycles | 2 BRAM, 4 DSP, 2776 FF, 2023 LUT | 3.530 ns |
| allocator | 265/265 PASS | 1012--1440 cycles | 0 BRAM, 46 DSP, 18593 FF, 10028 LUT | 3.853 ns |

All generated Verilog directories also compile with Icarus Verilog. The
allocator contains exactly one shared direct-cost core. CA2 and feedback beta
also share one exact rolled 136/128-bit restoring divider instead of using two
fully spatially-unrolled HLS dividers. Its worst HLS latency estimate is 62226
cycles, leaving 37774 cycles (37.8%) in the default 100000-cycle profiling
window, but it does not meet the 10000-cycle sensitivity point.

The 100-seed synthetic allocation sweep reports 100% agreement for Q4.8/R16,
no factor saturation, candidate-objective MAE 0.0030, and maximum candidate
error 0.0138. The minimum reference decision margin is 0.506. Q4.8/R12 also
keeps 100% allocation agreement, but is rejected because its direct-cost error
exceeds the existing 0.03% threshold on the standalone cost vectors.

Relative to the original two-divider Q8.16 allocator (80 DSP, 30473 FF, 12639
LUT), Q4.8/R16 with the shared rolled divider reduces DSP by 42.5%, FF by
39.0%, and LUT by 20.7%. Relative to the Q8.16/R20 shared-divider point, it
reduces DSP by 4.2%, FF by 17.3%, LUT by 20.5%, and worst latency by 13.5%.
The 265 C/RTL cosimulation transactions include 256 deterministic-random CA2
vectors checked bit-exactly against truncating `ap_uint` division.

Relative to the earlier exact direct-cost divider baseline, the reciprocal-cost
allocator removes 3 BRAMs, reduces FF by 46.5%, worst-case cycles by 26.5%,
and observed average cycles by 28.4%. The tradeoff is 11.1% more DSPs and
47.5% more LUTs.
These FPGA categories do not directly predict ASIC standard-cell area; both
implementations must be synthesized with the same ASIC library and constraints.

## F1/F2/F4 precision sweep on July 16, 2026

The controlled sweep fixes R16 and the shared rolled-divider architecture.
All three variants pass 265/265 C/Verilog cosimulation and Icarus compilation.

| Precision | DSP | FF | LUT | Estimated period | Worst latency |
|---|---:|---:|---:|---:|---:|
| F1 / Q4.1 | 46 | 18892 | 11056 | 3.853 ns | 59917 cycles |
| F2 / Q4.2 | 46 | 19006 | 11091 | 3.853 ns | 60277 cycles |
| F4 / Q4.4 | 46 | 17710 | 10532 | 3.853 ns | 60986 cycles |

The results are non-monotonic because HLS scheduling and multiplier mapping
dominate the small width difference. See
`../openroad/asap7/precision_sweep_results_2026-07-16.md` for the controlled
accuracy and ASAP7 comparison.

## Q4.4 range-width sweep on July 16, 2026

The follow-up experiment fixes arithmetic precision at Q4.4, keeps the
utilization guard independently at Q1.8, and keeps R16. It derives coherent
integer widths from the supported maximum profiling window instead of changing
only fractional bits. The 100K profile reduces packet count to 17 bits, R1/R2
to 22/27 bits, total-cost integer width to 34 bits, and the shared-divider
integer datapath to 51 bits, versus 32, 56/80, 84, and 128 bits in the current
wide implementation.

A 10,000-seed, 100K-cycle software pre-simulation gives 100% allocation
agreement, zero saturation, and zero regret for the W100K, W200K, W1M, and
legacy-wide profiles. The W100K point is therefore the minimum coherent HLS
candidate for the default window. A separate Lossy16 implementation point
deliberately narrows R1/R2 and cost/score ranges below their proofs to expose
the accuracy-versus-area failure boundary. See
`../range_width_sweep_results_2026-07-16.md` and
`../range_width_sweep_q4_10000.json`.

The allocator was then synthesized at Lossy16, W100K, W1M, and legacy-wide
ranges. All four passed C simulation, complete C/Verilog cosimulation (266/266
for Lossy16 and 265/265 for the other points), and independent Icarus
compilation:

| Range | DSP | FF | LUT | Estimated period | Worst latency |
|---|---:|---:|---:|---:|---:|
| Lossy16 | 19 | 5,390 | 5,130 | 4.254 ns | 29,469 cycles |
| W100K | 26 | 6,773 | 6,184 | 4.576 ns | 29,898 cycles |
| W1M | 29 | 6,867 | 6,172 | 4.710 ns | 33,557 cycles |
| Legacy wide | 46 | 17,714 | 10,573 | 3.853 ns | 60,986 cycles |

W100K leaves 70.1% worst-case cycle headroom in the default 100K window. Its
ASAP7 post-global-route allocator area is 5,210.88 um^2, 71.78% below the
controlled legacy-wide implementation. The complete HLS/ASAP7 comparison and
interface-packing limitations are in
`../range_width_sweep_results_2026-07-16.md`.

Lossy16 saves another 16.97% allocator area relative to W100K, but it violates
the supported dynamic range and is retained only as a decision-failure
control. Saturation invalidates the affected candidate instead of wrapping.
In the 10,000-seed diverse stress scan, Lossy16 agrees with the ideal oracle in
74.410% of windows and with W100K in 74.170%; W100K, W1M, and legacy wide agree
with each other in 100% and with the oracle in 99.410%. The remaining common
0.590% is Q4.4/R16 approximation error rather than range saturation.

The FPGA resources remain correctness/scheduling estimates and the ASAP7
results are post-global-route rather than signoff. The rolled CA2/feedback
divider is an area-first design point; the W100K implementation does not target
profiling windows shorter than its 29,898-cycle worst-case estimate.
The compact collector accepts a transaction every 13--37 cycles; therefore an
ASIC integration must place an independently sized event FIFO or replicated
ingress counters between packet observation and this engine. Until that FIFO is
implemented and sized from trace burst rates, this design does not claim one
event per cycle and must not backpressure the packet data path.
