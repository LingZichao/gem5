#!/usr/bin/env bash
set -euo pipefail

readonly script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly variant_root="${UACC_HLS_WIDTH_VARIANT_ROOT:-/mnt/e/uacc_hls/width_variants}"

variants=("$@")
if [[ ${#variants[@]} -eq 0 ]]; then
    variants=(lossy16 w100k w1m legacy)
fi

for variant in "${variants[@]}"; do
    case "$variant" in
        lossy16|w100k|w1m|legacy) ;;
        *)
            printf 'Unsupported range-width variant: %s\n' "$variant" >&2
            exit 2
            ;;
    esac

    printf 'Running allocator HLS for %s (Q4.4/R16)\n' "$variant"
    UACC_HLS_STAGE_DIR="${variant_root}/${variant}" \
    UACC_HLS_START_STAGE=allocator \
    UACC_HLS_FRACTION_BITS=4 \
    UACC_HLS_RANGE_PROFILE="$variant" \
        "$script_dir/run_vitis_hls_from_wsl.sh"
done

printf 'Range-width HLS outputs: %s\n' "$variant_root"
