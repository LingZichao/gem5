#!/usr/bin/env bash
set -euo pipefail

container="${UACC_ORFS_CONTAINER:-uacc-orfs}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
work_dir="/work/util/uacc_hw/openroad/work_full"

if ! docker inspect --format '{{.State.Running}}' "${container}" 2>/dev/null | grep -qx true; then
    echo "error: Docker container '${container}' is not running" >&2
    exit 1
fi

run_design() {
    local design="$1"
    local rtl_dir="/uacc_hls/uacc_section14_${design}/solution1/syn/verilog"
    local config="/work/util/uacc_hw/openroad/asap7/${design}/config.mk"
    local nickname="uacc_section14_${design}"
    local final_artifact="${work_dir}/results/asap7/${nickname}/base/6_final.gds"
    if [[ "${design}" == "allocator" ]]; then
        final_artifact="${work_dir}/results/asap7/${nickname}/base/6_final.odb"
    fi

    echo "Running ASAP7 PPA flow for ${nickname}"
    docker exec "${container}" bash -lc "
        set -euo pipefail
        test -n \"\$(find '${rtl_dir}' -maxdepth 1 -name '*.v' -print -quit)\"
        mkdir -p '${work_dir}'
        cd /OpenROAD-flow-scripts/flow
        make DESIGN_CONFIG='${config}' WORK_HOME='${work_dir}' clean_all
        make DESIGN_CONFIG='${config}' WORK_HOME='${work_dir}'
        test -s '${final_artifact}'
    "
}

designs=("$@")
if [[ ${#designs[@]} -eq 0 ]]; then
    designs=(allocator collector)
fi

for design in "${designs[@]}"; do
    case "${design}" in
        allocator|collector) run_design "${design}" ;;
        *)
            echo "error: expected 'allocator' or 'collector', got '${design}'" >&2
            exit 2
            ;;
    esac
done

echo "ASAP7 full-controller results: ${repo_root}/util/uacc_hw/openroad/work_full"
