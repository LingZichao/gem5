export PLATFORM = asap7

export DESIGN_NAME = uacc_section14_allocator_uacc_section14_allocator
export DESIGN_NICKNAME = uacc_section14_allocator_precision_sweep

# Select one immutable HLS variant at make invocation time, for example:
# UACC_PRECISION=f2 make DESIGN_CONFIG=.../precision_sweep.mk
UACC_PRECISION ?= f1
UACC_VARIANT_ROOT ?= /uacc_hls/variants
export VERILOG_FILES = $(sort $(wildcard $(UACC_VARIANT_ROOT)/$(UACC_PRECISION)/uacc_section14_allocator/solution1/syn/verilog/*.v))
export SDC_FILE = /work/util/uacc_hw/openroad/asap7/allocator/constraint.sdc

export CORE_UTILIZATION = 25
export CORE_ASPECT_RATIO = 1
export CORE_MARGIN = 3
export PLACE_DENSITY = 0.40

export SKIP_LAST_GASP ?= 1
export SYNTH_USE_SYN = 1
export SKIP_CTS_REPAIR_TIMING = 1

# Match the existing allocator methodology: global-route parasitics are used
# because detailed routing the flat design exceeds this host's memory budget.
export SKIP_DETAILED_ROUTE = 1
