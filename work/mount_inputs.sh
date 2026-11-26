#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

base=/tmp/codex-oneui-2205
reno=${RENO_FIRMWARE:-"$PWD/inputs/reno"}
fuse="$PWD/tools/erofs/usr/bin/erofsfuse"
[[ -x $fuse ]] || fuse=erofsfuse

mount_image() {
    local image=$1 mountpoint=$2
    mkdir -p "$mountpoint"
    if ! mountpoint -q "$mountpoint"; then
        "$fuse" -o ro "$image" "$mountpoint"
    fi
}

for partition in system system_ext product vendor odm; do
    mount_image "$reno/$partition.img" "$base/reno/$partition"
    mount_image "$PWD/a05/$partition.img" "$base/a05/$partition"
done
