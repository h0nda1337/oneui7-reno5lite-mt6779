#!/bin/busybox sh
set -u
export PATH=/bin

# LK puts previous power events in four-byte big-endian device-tree cells.
save_cell() {
    node=$1
    field=$2
    path="/sys/firmware/devicetree/base/odm/pmic_$node/$field"
    [ -r "$path" ] || return 0
    set -- $(od -An -N4 -tx1 "$path")
    [ "$#" -eq 4 ] || return 0
    printf 'PMIC %s.%s=%s%s%s%s\n' "$node" "$field" "$1" "$2" "$3" "$4"
}

(
    save_cell history_count count
    for node in history0 history1 history2 history3; do
        for field in data_is_valid chip_code pon_reason poff_reason poff_reason2 \
                     pg_sdn_sts0 pg_sdn_sts1 oc_sdn_sts0 oc_sdn_sts1 \
                     vr_sdn_mode0_1 vr_sdn_mode2_3 vr_sdn_mode4_5 vr_sdn_mode6_7 \
                     thermalstatus strup_con4 strup_con12 wdtrstb; do
            save_cell "$node" "$field"
        done
    done
) >/dev/a05-pmic.tmp
mv /dev/a05-pmic.tmp /dev/a05-pmic.log

{
    sha256sum /system/bin/bootstrap/linker64 /system/bin/init /system/bin/toybox
    timeout 7 /bin/check-a05-userspace
    status=$?
    printf 'A05_CHECK_EXIT=%s\n' "$status"
} >/dev/a05-userspace.tmp 2>&1
mv /dev/a05-userspace.tmp /dev/a05-userspace.log
printf '<5>CPH2205 A05 RAM check exit=%s\n' "$status" >/dev/kmsg
