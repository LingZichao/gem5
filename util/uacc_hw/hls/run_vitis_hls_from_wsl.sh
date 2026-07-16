#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly STAGE_DIR="${UACC_HLS_STAGE_DIR:-/mnt/e/uacc_hls}"
readonly VITIS_ROOT_WIN="${VITIS_ROOT_WIN:-E:\\Xilinx\\Vitis\\2019.2}"
readonly START_STAGE="${UACC_HLS_START_STAGE:-cost}"
readonly FRACTION_BITS="${UACC_HLS_FRACTION_BITS:-8}"
readonly RANGE_PROFILE="${UACC_HLS_RANGE_PROFILE:-default}"

case "$FRACTION_BITS" in
    1|2|4|8|16) ;;
    *)
        printf 'Invalid UACC_HLS_FRACTION_BITS: %s\n' "$FRACTION_BITS" >&2
        exit 2
        ;;
esac

case "$RANGE_PROFILE" in
    default) range_profile_value=-1 ;;
    legacy) range_profile_value=0 ;;
    lossy16) range_profile_value=16 ;;
    w100k) range_profile_value=100 ;;
    w1m) range_profile_value=1000 ;;
    *)
        printf 'Invalid UACC_HLS_RANGE_PROFILE: %s\n' "$RANGE_PROFILE" >&2
        exit 2
        ;;
esac

case "$START_STAGE" in
    cost)
        tops=(cost collector allocator)
        ;;
    cost_only)
        tops=(cost)
        ;;
    collector|allocator)
        tops=("$START_STAGE")
        ;;
    *)
        printf 'Invalid UACC_HLS_START_STAGE: %s\n' "$START_STAGE" >&2
        exit 2
        ;;
esac

mkdir -p "$STAGE_DIR"
for source in \
    uacc_section14_widths.hpp \
    uacc_section14_cost.hpp \
    uacc_section14_cost.cpp \
    uacc_section14_cost_test.cpp \
    uacc_section14_collector.hpp \
    uacc_section14_collector.cpp \
    uacc_section14_collector_test.cpp \
    uacc_section14_allocator.hpp \
    uacc_section14_allocator.cpp \
    uacc_section14_allocator_test.cpp \
    run_hls.tcl; do
    install -m 0644 "$SCRIPT_DIR/$source" "$STAGE_DIR/$source"
done

cd "$STAGE_DIR"
cmd.exe /d /s /c \
    "set UACC_HLS_START_STAGE=${START_STAGE}&& set UACC_HLS_FRACTION_BITS=${FRACTION_BITS}&& set UACC_HLS_RANGE_PROFILE=${range_profile_value}&& call ${VITIS_ROOT_WIN}\\settings64.bat >nul && vitis_hls -f run_hls.tcl"

for top in "${tops[@]}"; do
    project_name="uacc_section14_${top}"
    project_dir="$STAGE_DIR/$project_name"
    report="$project_dir/solution1/syn/report/${project_name}_csynth.rpt"
    verilog_dir="$project_dir/solution1/syn/verilog"
    cosim_report_dir="$project_dir/solution1/sim/report"
    if [[ ! -s "$report" ]]; then
        printf 'Missing HLS output: %s\n' "$report" >&2
        exit 1
    fi
    verilog="$(find "$verilog_dir" -maxdepth 1 -type f \
        -name "*${project_name}.v" -print -quit)"
    cosim_report="$(find "$cosim_report_dir" -maxdepth 1 -type f \
        -name "*${project_name}_cosim.rpt" -print -quit)"
    if [[ -z "$verilog" || ! -s "$verilog" ]]; then
        printf 'Missing top-level Verilog under: %s\n' "$verilog_dir" >&2
        exit 1
    fi
    if [[ -z "$cosim_report" || ! -s "$cosim_report" ]]; then
        printf 'Missing cosimulation report under: %s\n' \
            "$cosim_report_dir" >&2
        exit 1
    fi
    if ! grep -Eiq '\| *Verilog *\| *Pass *\|' "$cosim_report"; then
        printf 'Verilog cosimulation did not pass: %s\n' \
            "$cosim_report" >&2
        exit 1
    fi
    printf '%s Verilog: %s\n' "$project_name" "$verilog"
done
printf 'All HLS C/Verilog cosimulations: PASS\n'
