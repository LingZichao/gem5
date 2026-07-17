export PLATFORM = asap7

export DESIGN_NAME = uacc_allocator_uacc_allocator
export DESIGN_NICKNAME = uacc_allocator_width_sweep

# Select one immutable Q4.4/R16 range-width variant at make invocation time.
# Example: UACC_WIDTH_PROFILE=w100k make DESIGN_CONFIG=.../width_sweep.mk
UACC_WIDTH_PROFILE ?= w100k
UACC_WIDTH_VARIANT_ROOT ?= /uacc_hls/width_variants
export VERILOG_FILES = $(sort $(wildcard $(UACC_WIDTH_VARIANT_ROOT)/$(UACC_WIDTH_PROFILE)/uacc_allocator/solution1/syn/verilog/*.v))
export SDC_FILE = /work/uacc/hw/openroad/asap7/allocator/constraint.sdc

# Keep every physical-design setting identical to the controlled F1/F2/F4
# experiment so the comparison isolates integer and accumulator range widths.
export CORE_UTILIZATION = 25
export CORE_ASPECT_RATIO = 1
export CORE_MARGIN = 3
export PLACE_DENSITY = 0.40

export SKIP_LAST_GASP ?= 1
export SYNTH_USE_SYN = 1
export SKIP_CTS_REPAIR_TIMING = 1

# Use global-route parasitics: detailed routing the flat allocator exceeds the
# current host memory budget.
export SKIP_DETAILED_ROUTE = 1
