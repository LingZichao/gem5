# UACC Section 14 precision sweep

This experiment compares three fractional precisions with the integer range,
R16 reciprocal, shared restoring divider, HLS source, 5 ns constraint, and
ASAP7 physical-design settings held constant.

## Precision definition

| Label | CA2/beta | Utilization | Queue cost | Total cost |
|---|---:|---:|---:|---:|
| F1 | UQ4.1, 5 bits | UQ1.1, 2 bits | UQ80.1, 81 bits | 85 bits |
| F2 | UQ4.2, 6 bits | UQ1.2, 3 bits | UQ80.2, 82 bits | 86 bits |
| F4 | UQ4.4, 8 bits | UQ1.4, 5 bits | UQ80.4, 84 bits | 88 bits |

## Algorithm-level accuracy

The accuracy sweep uses 10,000 deterministic synthetic seeds, a 100,000-cycle
window, on/off plus Poisson traffic, and the floating-point causal allocator as
the oracle. It quantizes CA2/beta and uses the R16 reciprocal model. These are
not gem5 workload-trace results.

| Precision | Agreement | Mismatches | Objective MAE | P99 error | Max error | Mean regret | Conditional mismatch regret | Max regret | Saturations |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| F1 | 99.750% | 25 / 10,000 | 0.166026 | 0.742314 | 1.060807 | 0.001201 | 0.480585 | 0.562828 | 0 |
| F2 | 99.990% | 1 / 10,000 | 0.150842 | 0.632955 | 0.753797 | 0.000028 | 0.282753 | 0.282753 | 0 |
| F4 | 100.000% | 0 / 10,000 | 0.049231 | 0.200533 | 0.235447 | 0 | 0 | 0 | 0 |

The minimum floating-point decision margin across the sweep is 0.282753.
F1 is an intentionally aggressive point rather than a lossless format. F2 has
one boundary decision change. F4 is the only tested point with full agreement.

## HLS results

All variants target `xc7z020clg400-1` at 5 ns. Each passed C simulation,
265/265 C/Verilog cosimulation, and independent Icarus Verilog compilation.

| Precision | DSP | FF | LUT | Estimated period | HLS worst latency | Observed RTL latency |
|---|---:|---:|---:|---:|---:|---:|
| F1 | 46 | 18,892 | 11,056 | 3.853 ns | 59,917 cycles | 1,125--1,532 cycles |
| F2 | 46 | 19,006 | 11,091 | 3.853 ns | 60,277 cycles | 1,128--1,538 cycles |
| F4 | 46 | 17,710 | 10,532 | 3.853 ns | 60,986 cycles | 1,131--1,546 cycles |

HLS resources are not monotonic with precision. Scheduling and multiplier
mapping dominate the few bits removed between these points, so FPGA LUT/FF
counts must not be used as a substitute for the ASIC comparison.

## ASAP7 allocator PPA

The allocator results use ASAP7 RVT at 0.77 V and a 5 ns clock. They include
placement, CTS, and global routing. Detailed routing is skipped, so timing and
power use global-route parasitic estimates and are not signoff results.

| Precision | Cell area | Std cells | Die area | Setup slack | Hold slack | Fmax | Internal | Switching | Leakage | Total power |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| F1 | 18,356.7 um^2 | 134,735 | 77,560.0 um^2 | +2,481.14 ps | +2.613 ps | 397.006 MHz | 97.977 mW | 67.524 mW | 0.0139 mW | 165.515 mW |
| F2 | 18,551.0 um^2 | 135,924 | 78,303.7 um^2 | +2,509.97 ps | +3.156 ps | 401.602 MHz | 100.894 mW | 67.436 mW | 0.0141 mW | 168.344 mW |
| F4 | 18,431.8 um^2 | 135,268 | 77,906.9 um^2 | +2,628.53 ps | +0.751 ps | 421.680 MHz | 65.994 mW | 44.196 mW | 0.0141 mW | 110.204 mW |

All three variants have zero setup TNS and zero hold TNS at this stage. Their
cell areas differ by only 1.06% from minimum to maximum and are not monotonic
with fractional width. F4 has the best accuracy and Fmax, while F1 has only a
0.41% area advantage over F4. The F4 power result is substantially lower, but
power uses OpenROAD default activity rather than workload VCD/SAIF and must be
confirmed with common trace-driven activity before drawing a dynamic-power
conclusion.

## Combined controller

The collector is precision-independent and is reused from the detailed-route
run: 4,133.5 um^2, 4.533 mW default-activity power, and 456.775 MHz Fmax. The
allocator already contains the direct-cost core, so it is not counted again.

| Precision | Controller area | Setup slack | Hold slack | Controller Fmax | Default-activity power |
|---|---:|---:|---:|---:|---:|
| F1 | 22,490.2 um^2 (0.022490 mm^2) | +2,481.14 ps | +2.613 ps | 397.006 MHz | 170.048 mW |
| F2 | 22,684.5 um^2 (0.022685 mm^2) | +2,509.97 ps | +3.156 ps | 401.602 MHz | 172.877 mW |
| F4 | 22,565.3 um^2 (0.022565 mm^2) | +2,628.53 ps | +0.751 ps | 421.680 MHz | 114.737 mW |

Relative to the earlier 0.084107 mm^2 controller, these implementations are
about 73% smaller. That comparison changes both divider architecture and
precision: most of the reduction must be attributed to replacing the earlier
spatial HLS divider with the shared rolled divider, not to precision alone.
The controlled F1/F2/F4 comparison shows that reducing fractional precision
below F4 saves less than 0.5% area and introduces allocation disagreement.

## Reproduction and limitations

- Accuracy JSON: `util/uacc_hw/fixed_point_sweep_f1_f2_f4_10000.json`.
- HLS runner: `util/uacc_hw/hls/run_precision_sweep.sh`.
- OpenROAD runner: `util/uacc_hw/openroad/run_precision_sweep.sh`.
- Archived reports: `util/uacc_hw/openroad/asap7/precision_sweep_results/`.
- ATD, configuration/snapshot memories, event FIFO, and top-level integration
  remain outside these HLS tops.
- Real gem5 traces, a common VCD/SAIF activity source, and detailed routing are
  still required for workload power and signoff claims.
