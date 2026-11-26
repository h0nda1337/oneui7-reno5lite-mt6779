#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

root=/tmp/codex-oneui-2205
compiler="$PWD/tools/selinux/usr/bin/secilc"
[[ -x $compiler ]] || compiler=secilc
out="$PWD/policy"
mkdir -p "$out"

vendor="$root/reno/vendor/etc/selinux"
odm="$root/reno/odm/etc/selinux"
version=$(cat "$vendor/plat_sepolicy_vers.txt")
policy_version=$(python3 - "$odm/precompiled_sepolicy" <<'PY'
import struct
import sys
with open(sys.argv[1], "rb") as policy:
    magic, name_length = struct.unpack("<II", policy.read(8))
    if magic != 0xf97cff8c or not 1 <= name_length <= 64:
        raise ValueError("Invalid SELinux policy header")
    policy.seek(name_length, 1)
    print(struct.unpack("<I", policy.read(4))[0])
PY
)
echo "Reno policy mapping $version; binary policy format $policy_version"

compile_system() {
    local device=$1
    local variant=${2:-stock}
    local plat="$root/$device/system/system/etc/selinux"
    local ext="$root/$device/system_ext/etc/selinux"
    local product="$root/$device/product/etc/selinux"
    local ext_maps="$ext/mapping"
    case "$variant" in
        no-ext-map) ext_maps="$out/nonexistent" ;;
        reno-ext-map) ext_maps="$root/reno/system_ext/etc/selinux/mapping" ;;
        filtered-ext-map) ext_maps="$out/a05-filtered-map" ;;
    esac
    local args=("$plat/plat_sepolicy.cil" "$plat/mapping/$version.cil")
    local optional
    for optional in "$plat/mapping/$version.compat.cil" \
        "$ext/system_ext_sepolicy.cil" "$ext_maps/$version.cil" \
        "$ext_maps/$version.compat.cil" "$product/product_sepolicy.cil" \
        "$product/mapping/$version.cil" "$product/mapping/$version.compat.cil"; do
        if [[ -f "$optional" ]]; then
            args+=("$optional")
        fi
    done
    args+=("$vendor/plat_pub_versioned.cil" "$vendor/vendor_sepolicy.cil")
    if [[ -f "$odm/odm_sepolicy.cil" ]]; then
        args+=("$odm/odm_sepolicy.cil")
    fi
    "$compiler" -m -M true -G -N -c "$policy_version" "${args[@]}" \
        -o "$out/$device-$variant-with-reno-vendor.bin" -f /dev/null >"$out/$device-$variant.log" 2>&1
}

report() {
    local device=$1 variant=$2
    if compile_system "$device" "$variant"; then
        echo "$device ($variant) + Reno vendor: CIL compilation succeeded"
    else
        echo "$device ($variant) + Reno vendor: CIL compilation failed"
        head -n 20 "$out/$device-$variant.log"
    fi
}

report reno stock
report a05 stock
report a05 no-ext-map
report a05 reno-ext-map
python3 filter_ext_mapping.py
report a05 filtered-ext-map
