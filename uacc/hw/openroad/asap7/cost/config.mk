export PLATFORM = asap7

export DESIGN_NAME = uacc_cost_uacc_cost
export DESIGN_NICKNAME = uacc_cost

export VERILOG_FILES = $(sort $(wildcard /uacc_hls/uacc_cost/solution1/syn/verilog/*.v))
export SDC_FILE = /work/uacc/hw/openroad/asap7/cost/constraint.sdc

# Conservative floorplan settings for the first end-to-end ASAP7 run.
export CORE_UTILIZATION = 30
export CORE_ASPECT_RATIO = 1
export CORE_MARGIN = 2
export PLACE_DENSITY = 0.45

export SKIP_LAST_GASP ?= 1
export SYNTH_USE_SYN = 1

# The current ORFS image traps in the optional post-CTS detailed-placement
# check. CTS itself remains enabled; only its repair/check pass is skipped for
# this smoke configuration.
export SKIP_CTS_REPAIR_TIMING = 1
