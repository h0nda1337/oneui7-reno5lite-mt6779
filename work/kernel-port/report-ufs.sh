#!/bin/busybox sh

sysfs=${1:-/sys}
wait_seconds=${2:-12}
controller="$sysfs/bus/platform/devices/11270000.ufshci"

if [ ! -d "$controller" ]; then
    echo "UFS controller 11270000 absent"
    exit 1
fi

controller_path=$(readlink -f "$controller") || exit 1

inventory_disks() {
    for disk in "$sysfs"/class/block/*; do
        [ -e "$disk" ] && [ ! -e "$disk/partition" ] || continue
        disk_path=$(readlink -f "$disk") || continue
        case "$disk_path" in
            "$controller_path"/host*/target*/*/block/*) ;;
            *) continue ;;
        esac
        dev_dir=${disk_path%/block/*}
        lun=${dev_dir##*:}
        read -r sectors 2>/dev/null <"$disk/size" || continue
        case "$sectors" in
            ''|*[!0-9]*|0) continue ;;
        esac
        printf 'UFS disk %s lun=%s sectors512=%s\n' "${disk##*/}" "$lun" "$sectors"

        for part in "$disk_path"/*/partition; do
            [ -f "$part" ] || continue
            part_dir=${part%/*}
            for label in super userdata; do
                if grep -Fxq "PARTNAME=$label" "$part_dir/uevent" 2>/dev/null; then
                    printf 'UFS partition %s on %s\n' "$label" "${part_dir##*/}"
                fi
            done
        done
    done
}

elapsed=0
while :; do
    driver=$(readlink "$controller/driver" 2>/dev/null)
    if [ "${driver##*/}" = ufshcd-mtk ]; then
        inventory=$(inventory_disks)
        if printf '%s\n' "$inventory" | grep -q '^UFS partition super on ' &&
           printf '%s\n' "$inventory" | grep -q '^UFS partition userdata on '; then
            printf 'UFS driver ufshcd-mtk\n%s\n' "$inventory"
            exit 0
        fi
    fi
    if [ "$elapsed" -ge "$wait_seconds" ]; then
        if [ "${driver##*/}" = ufshcd-mtk ]; then
            echo 'UFS driver ufshcd-mtk'
            [ -z "$inventory" ] || printf '%s\n' "$inventory"
            echo "UFS super/userdata scan incomplete after ${elapsed}s"
        else
            echo "UFS driver not bound after ${elapsed}s"
        fi
        exit 1
    fi
    sleep 1
    elapsed=$((elapsed + 1))
done
