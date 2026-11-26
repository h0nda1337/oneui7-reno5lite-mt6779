#!/usr/bin/env bash
set -euo pipefail
root=/tmp/codex-oneui-2205

echo "Hybrid policy failure site:"
sed -n '455,470p' "$root/a05/system_ext/etc/selinux/mapping/31.0.cil"
echo "Original OPPO system_ext public mapping:"
head -n 10 "$root/reno/system_ext/etc/selinux/mapping/31.0.cil"
echo "Original OPPO DSU components:"
find "$root/reno" -maxdepth 6 \( -iname 'gsid' -o -iname 'gsid.rc' -o -iname '*dynsystem*' \
    -o -iname '*dynamic_system*' -o -iname '*gsikey*' -o -iname '*gsi*.avbpubkey' \) | sort
echo "OPPO logical system mounts:"
awk '$2 ~ /^\/(system|product|vendor|odm|my_|data|metadata)/ {print}' \
    "$root/reno/vendor/etc/fstab.mt6779"
echo "Stock SELinux hash comparison:"
for device in reno a05; do
    path="$root/$device/system/system/etc/selinux/plat_sepolicy_and_mapping.sha256"
    printf '%s platform: ' "$device"
    cat "$path"
    printf '\n'
done
printf 'OPPO precompiled: '
cat "$root/reno/odm/etc/selinux/precompiled_sepolicy.plat_sepolicy_and_mapping.sha256"
printf '\n'
