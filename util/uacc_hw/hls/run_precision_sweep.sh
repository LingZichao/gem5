#!/usr/bin/env bash
set -euo pipefail

readonly script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly variant_root="${UACC_HLS_VARIANT_ROOT:-/mnt/e/uacc_hls/variants}"

variants=("$@")
if [[ ${#variants[@]} -eq 0 ]]; then
    variants=(f1 f2 f4)
fi

for variant in "${variants[@]}"; do
    case "$variant" in
        f1) fraction_bits=1 ;;
        f2) fraction_bits=2 ;;
        f4) fraction_bits=4 ;;
        *)
            printf 'Unsupported precision variant: %s\n' "$variant" >&2
            exit 2
            ;;
    esac

    printf 'Running allocator HLS for %s (Q4.%s/R16)\n' \
        "$variant" "$fraction_bits"
    UACC_HLS_STAGE_DIR="${variant_root}/${variant}" \
    UACC_HLS_START_STAGE=allocator \
    UACC_HLS_FRACTION_BITS="$fraction_bits" \
        "$script_dir/run_vitis_hls_from_wsl.sh"
done

printf 'Precision-sweep HLS outputs: %s\n' "$variant_root"
