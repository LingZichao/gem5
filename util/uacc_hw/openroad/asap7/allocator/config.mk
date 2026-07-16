export PLATFORM = asap7

export DESIGN_NAME = uacc_section14_allocator_uacc_section14_allocator
export DESIGN_NICKNAME = uacc_section14_allocator

export VERILOG_FILES = $(sort $(wildcard /uacc_hls/uacc_section14_allocator/solution1/syn/verilog/*.v))
export SDC_FILE = /work/util/uacc_hw/openroad/asap7/allocator/constraint.sdc

export CORE_UTILIZATION = 25
export CORE_ASPECT_RATIO = 1
export CORE_MARGIN = 3
export PLACE_DENSITY = 0.40

export SKIP_LAST_GASP ?= 1
export SYNTH_USE_SYN = 1
export SKIP_CTS_REPAIR_TIMING = 1

# The flat allocator maps to roughly 0.5M cells. Detailed routing requires far
# more than the 15.5 GiB available on the current host, so allocator PPA uses
# global-route parasitics.
export SKIP_DETAILED_ROUTE = 1
