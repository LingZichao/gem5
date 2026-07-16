#!/usr/bin/env bash
set -euo pipefail

container="${UACC_ORFS_CONTAINER:-uacc-orfs}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
config="/work/util/uacc_hw/openroad/asap7/cost/config.mk"
work_dir="/work/util/uacc_hw/openroad/work"
rtl_dir="/uacc_hls/uacc_section14_cost/solution1/syn/verilog"
final_gds="${work_dir}/results/asap7/uacc_section14_cost/base/6_final.gds"

if ! docker inspect --format '{{.State.Running}}' "${container}" 2>/dev/null | grep -qx true; then
    echo "error: Docker container '${container}' is not running" >&2
    exit 1
fi

docker exec "${container}" bash -lc "
    set -euo pipefail
    test -n \"\$(find '${rtl_dir}' -maxdepth 1 -name '*.v' -print -quit)\"
    mkdir -p '${work_dir}'
    cd /OpenROAD-flow-scripts/flow
    make DESIGN_CONFIG='${config}' WORK_HOME='${work_dir}' clean_all
    make DESIGN_CONFIG='${config}' WORK_HOME='${work_dir}'
    test -s '${final_gds}'
"

echo "ASAP7 smoke results: ${repo_root}/util/uacc_hw/openroad/work"
