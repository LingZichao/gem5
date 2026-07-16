export PLATFORM = asap7

export DESIGN_NAME = uacc_section14_collector_uacc_section14_collector
export DESIGN_NICKNAME = uacc_section14_collector

export VERILOG_FILES = $(sort $(wildcard /uacc_hls/uacc_section14_collector/solution1/syn/verilog/*.v))
export SDC_FILE = /work/util/uacc_hw/openroad/asap7/collector/constraint.sdc

export CORE_UTILIZATION = 30
export CORE_ASPECT_RATIO = 1
export CORE_MARGIN = 2
export PLACE_DENSITY = 0.45

export SKIP_LAST_GASP ?= 1
export SYNTH_USE_SYN = 1
export SKIP_CTS_REPAIR_TIMING = 1
