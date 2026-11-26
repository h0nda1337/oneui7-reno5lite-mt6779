#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

base=/tmp/codex-oneui-2205/a05/system_ext
mapping=etc/selinux/mapping/31.0.cil
candidate="$PWD/dsu"
tools="$PWD/tools/erofs/usr/bin"
[[ -x $tools/mkfs.erofs ]] || tools=$(dirname "$(command -v mkfs.erofs)")
rebuilt="$candidate/system_ext.img"
staging="$candidate/system_ext.partial"
mountpoint -q "$base"
mkdir -p "$candidate"

tar --format=pax --numeric-owner --xattrs --xattrs-include='*' -C "$base" -cf - . |
    python3 replace_tar_file.py "$mapping" policy/a05-filtered-map/31.0.cil |
    "$tools/mkfs.erofs" --tar=f -T 1640995200 --mkfs-time "$staging"

"$tools/fsck.erofs" --extract "$staging"
mv -f -- "$staging" "$rebuilt"
