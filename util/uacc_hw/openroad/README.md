# UACC OpenROAD PPA flow

The initial smoke test runs the Vitis HLS `uacc_section14_cost` RTL through the
complete OpenROAD-flow-scripts ASAP7 flow with a conservative 2000 ps (500 MHz)
target clock. Tighter clocks belong in the subsequent frequency sweep.

This smoke configuration skips the optional post-CTS timing-repair pass because
the current ORFS image exits with `illegal instruction` in its detailed
placement check. Clock-tree synthesis is still run. A production PPA run must
remove this workaround after the image/runtime issue is resolved.

The `uacc-orfs` container must mount the gem5 repository at `/work` and the HLS
output directory at `/uacc_hls` (read-only). Run:

```sh
util/uacc_hw/openroad/run_asap7_smoke.sh
```

Generated logs, reports, and physical-design results are placed under `work/`
and are intentionally not tracked.

## Full controller run

The full Section 14 controller evaluation runs the allocator and collector as
separate physical blocks with the same ASAP7 RVT platform and 5000 ps clock.
The allocator already contains the direct-cost core, so the standalone cost
block is not counted again.

```sh
util/uacc_hw/openroad/run_asap7_full.sh
```

Results are written under `work_full/`. The flat allocator is stopped after
global routing because detailed-routing the roughly 0.5M-cell design would exceed
the current 15.5 GiB host memory; its timing and power therefore use estimated
global-route parasitics. The smaller collector still completes detailed routing
and GDS generation. Allocator memory ports remain the boundary to the ATD,
configuration tables, snapshot banks, and result arrays;
their storage area and activity are not included by this HLS top and must be
reported separately from the arithmetic/controller PPA.

To resume or rerun only one block, pass its name:

```sh
util/uacc_hw/openroad/run_asap7_full.sh allocator
util/uacc_hw/openroad/run_asap7_full.sh collector
```

## Precision sweep

After generating the F1/F2/F4 HLS variants, run the allocator sweep with:

```sh
util/uacc_hw/hls/run_precision_sweep.sh
util/uacc_hw/openroad/run_precision_sweep.sh
```

The OpenROAD runner uses a common 5 ns SDC and the same ASAP7 allocator
configuration for all three variants. Reports and metric JSON are archived
under `asap7/precision_sweep_results/{f1,f2,f4}/`. The consolidated accuracy,
HLS, allocator PPA, and combined-controller tables are in
`asap7/precision_sweep_results_2026-07-16.md`.

Each allocator run can require several GiB of temporary storage. The default
runner executes variants sequentially in one work directory. Separate
`UACC_PPA_WORK_DIR` values are required for intentional concurrent runs, and
concurrency must be limited to the host memory budget.

## Integer range-width sweep

The range-width experiment holds Q4.4 cost/CA2/beta arithmetic, independent
Q1.8 utilization, R16 reciprocal logic, interfaces, outputs, 5 ns SDC, and all
ASAP7 physical-design settings constant. It compares a deliberately lossy
16/20-bit R1/R2 failure point, the safe default 100K-cycle bound, a conservative
1M-cycle bound, and the legacy wide accumulator types:

```sh
util/uacc_hw/hls/run_width_sweep.sh
util/uacc_hw/openroad/run_width_sweep.sh
```

Reports are archived under
`asap7/width_sweep_results/{lossy16,w100k,w1m,legacy}/`. The allocator remains a
post-global-route comparison; power is based on default activity and is not a
workload-average or signoff result. The consolidated software, HLS, allocator,
and projected-controller results are in
`../range_width_sweep_results_2026-07-16.md`.

Lossy16 reaches only 74.170% decision agreement with the safe W100K point. Its
allocator area is 4,326.72 um^2, 16.97% below W100K, while projected controller
area is 0.008460 mm^2, only 9.46% below W100K because the collector is unchanged.
