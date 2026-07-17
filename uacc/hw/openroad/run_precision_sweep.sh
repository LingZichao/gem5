#!/usr/bin/env bash
set -euo pipefail

readonly container="${UACC_ORFS_CONTAINER:-uacc-orfs}"
readonly config="/work/uacc/hw/openroad/asap7/allocator/precision_sweep.mk"
readonly work_dir="${UACC_PPA_WORK_DIR:-/work/uacc/hw/openroad/work_precision_sweep}"
readonly nickname="uacc_allocator_precision_sweep"
readonly archive_root="${UACC_PPA_ARCHIVE_ROOT:-/work/uacc/hw/openroad/asap7/precision_sweep_results}"

if ! docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null \
    | grep -qx true; then
    printf "error: Docker container '%s' is not running\n" "$container" >&2
    exit 1
fi

variants=("$@")
if [[ ${#variants[@]} -eq 0 ]]; then
    variants=(f1 f2 f4)
fi

for precision in "${variants[@]}"; do
    case "$precision" in
        f1|f2|f4) ;;
        *)
            printf 'error: unsupported precision: %s\n' "$precision" >&2
            exit 2
            ;;
    esac

    rtl_dir="/uacc_hls/variants/${precision}/uacc_allocator/solution1/syn/verilog"
    archive_dir="${archive_root}/${precision}"
    printf 'Running ASAP7 allocator PPA for %s\n' "$precision"
    docker exec "$container" bash -lc "
        set -euo pipefail
        test -n \"\$(find '${rtl_dir}' -maxdepth 1 -name '*.v' -print -quit)\"
        mkdir -p '${work_dir}' '${archive_dir}'
        cd /OpenROAD-flow-scripts/flow
        make DESIGN_CONFIG='${config}' WORK_HOME='${work_dir}' \
            UACC_PRECISION='${precision}' clean_all
        make DESIGN_CONFIG='${config}' WORK_HOME='${work_dir}' \
            UACC_PRECISION='${precision}'
        test -s '${work_dir}/results/asap7/${nickname}/base/6_final.odb'
        rm -rf '${archive_dir}/reports' '${archive_dir}/logs'
        cp -a '${work_dir}/reports/asap7/${nickname}/base' \
            '${archive_dir}/reports'
        cp -a '${work_dir}/logs/asap7/${nickname}/base' \
            '${archive_dir}/logs'
    "
done

printf 'ASAP7 precision sweep reports: %s\n' \
    'uacc/hw/openroad/asap7/precision_sweep_results'
