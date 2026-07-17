# UACC Section 14 ASAP7 PPA snapshot

> This snapshot uses the earlier Q8.16 allocator RTL. The later Q4.8/R16
> shared-divider HLS result is not reflected in the area, timing, or power
> numbers below and requires a separate OpenROAD run.

The subsequent controlled F1/F2/F4 shared-divider experiment is reported in
`precision_sweep_results_2026-07-16.md`; do not combine its numbers with this
older allocator implementation.

The latest fixed-Q4.4 integer range-width experiment is reported in
`../../range_width_sweep_results_2026-07-16.md`. Its W100K allocator is the
current area-oriented point and supersedes this snapshot for allocator PPA.

## Run definition

- Platform: OpenROAD-flow-scripts ASAP7 RVT, 0.77 V.
- Clock constraint: 5000 ps (200 MHz), matching the Vitis HLS target.
- Allocator: post-global-route estimates; detailed route skipped because the
  flat roughly 0.5M-cell design exceeds the 15.5 GiB host memory budget.
- Collector: detailed route, extraction, timing, and GDS completed.
- The allocator includes the direct-cost core. The standalone cost smoke block
  is not added again.

## Results

| Metric | Allocator | Collector | Combined controller |
|---|---:|---:|---:|
| Instance area (um^2) | 79,973.8 | 4,133.5 | 84,107.3 |
| Instance area (mm^2) | 0.079974 | 0.004134 | 0.084107 |
| Setup slack (ps) | 1,399.72 | 2,810.74 | 1,399.72 |
| Hold slack (ps) | -0.967 | 17.348 | -0.967 |
| Fmax (MHz) | 277.756 | 456.775 | 277.756 |
| Default-activity power (mW) | 415.534 | 4.533 | 420.067 |
| Internal power (mW) | 257.176 | 3.463 | 260.639 |
| Switching power (mW) | 158.304 | 1.068 | 159.372 |
| Leakage power (mW) | 0.0546 | 0.0028 | 0.0574 |
| Detailed-route DRC | Not run | 0 | Not signoff-clean |

The allocator has setup margin at 200 MHz but retains a -0.967 ps hold
violation (TNS -1.312 ps). It must not be reported as timing-clean.

## Scope limitations

- Power uses OpenROAD default activity, not gem5 VCD/SAIF. It is a flow result,
  not the final workload-driven power number.
- Allocator timing/power use global-route parasitic estimates. Its generated
  GDS is not a detailed-routed signoff layout.
- ATD, configuration tables, snapshot banks, candidate/result memories, event
  FIFO, and top-level integration are external to these HLS tops and excluded.
- The allocator exposes 45 floating/unconstrained endpoints and unused padded
  memory-interface bits. These require an integration wrapper before signoff.
- The collector's `packet_class[1:0]` is optimized as dangling in this HLS RTL;
  inspect the generated class-count interface before treating its PPA as final.
