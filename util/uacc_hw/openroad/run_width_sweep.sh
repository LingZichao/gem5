#!/usr/bin/env bash
set -euo pipefail

readonly container="${UACC_ORFS_CONTAINER:-uacc-orfs}"
readonly config="/work/util/uacc_hw/openroad/asap7/allocator/width_sweep.mk"
readonly work_dir="${UACC_PPA_WORK_DIR:-/work/util/uacc_hw/openroad/work_width_sweep}"
readonly nickname="uacc_section14_allocator_width_sweep"
readonly archive_root="${UACC_PPA_ARCHIVE_ROOT:-/work/util/uacc_hw/openroad/asap7/width_sweep_results}"

if ! docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null \
    | grep -qx true; then
    printf "error: Docker container '%s' is not running\n" "$container" >&2
    exit 1
fi

variants=("$@")
if [[ ${#variants[@]} -eq 0 ]]; then
    variants=(lossy16 w100k w1m legacy)
fi

for variant in "${variants[@]}"; do
    case "$variant" in
        lossy16|w100k|w1m|legacy) ;;
        *)
            printf 'error: unsupported width profile: %s\n' "$variant" >&2
            exit 2
            ;;
    esac

    rtl_dir="/uacc_hls/width_variants/${variant}/uacc_section14_allocator/solution1/syn/verilog"
    archive_dir="${archive_root}/${variant}"
    printf 'Running ASAP7 allocator PPA for %s (Q4.4/R16)\n' "$variant"
    docker exec "$container" bash -lc "
        set -euo pipefail
        test -n \"\$(find '${rtl_dir}' -maxdepth 1 -name '*.v' -print -quit)\"
        mkdir -p '${work_dir}' '${archive_dir}'
        cd /OpenROAD-flow-scripts/flow
        make DESIGN_CONFIG='${config}' WORK_HOME='${work_dir}' \\
            UACC_WIDTH_PROFILE='${variant}' clean_all
        make DESIGN_CONFIG='${config}' WORK_HOME='${work_dir}' \\
            UACC_WIDTH_PROFILE='${variant}'
        test -s '${work_dir}/results/asap7/${nickname}/base/6_final.odb'
        rm -rf '${archive_dir}/reports' '${archive_dir}/logs'
        cp -a '${work_dir}/reports/asap7/${nickname}/base' \\
            '${archive_dir}/reports'
        cp -a '${work_dir}/logs/asap7/${nickname}/base' \\
            '${archive_dir}/logs'
    "
done

printf 'ASAP7 width sweep reports: %s\n' \
    'util/uacc_hw/openroad/asap7/width_sweep_results'
