#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

source_dir=/var/tmp/codex-cph2205-linux-6.6.89
ramdisk=ramdisk.cpio.gz
variant=${1:-}
case "$variant" in
    recovery) ramdisk=ramdisk-recovery.cpio.gz ;;
    recovery-ufs-module) ramdisk=ramdisk-recovery-ufs-module.cpio.gz ;;
    recovery-ufs-on-request) ramdisk=ramdisk-recovery-ufs-on-request.cpio.gz ;;
    recovery-a05-ram) ramdisk=ramdisk-recovery-a05-ram.cpio.gz ;;
esac
ramdisk=${2:-$ramdisk}
busybox=${BUSYBOX_ARM64:-ramdisk-tools/usr/bin/busybox}
file "$busybox" | grep -F 'ARM aarch64'
gcc -O2 -o gen_init_cpio "$source_dir/usr/gen_init_cpio.c"

cat >ramdisk.filelist <<EOF
dir /bin 0755 0 0
dir /config 0755 0 0
dir /dev 0755 0 0
dir /proc 0555 0 0
dir /sys 0555 0 0
nod /dev/console 0600 0 0 c 5 1
nod /dev/tty 0666 0 0 c 5 0
nod /dev/ttyAMA0 0600 0 0 c 204 64
nod /dev/ttyS0 0600 0 0 c 4 64
nod /dev/null 0666 0 0 c 1 3
nod /dev/kmsg 0600 0 0 c 1 11
file /bin/busybox $busybox 0755 0 0
file /bin/report-ufs report-ufs.sh 0755 0 0
file /init init-early.sh 0755 0 0
EOF
if [[ $variant == recovery || $variant == recovery-ufs-module || $variant == recovery-ufs-on-request || $variant == recovery-a05-ram ]]; then
    printf 'file /recovery-diagnostic /dev/null 0444 0 0\n' >>ramdisk.filelist
fi
if [[ $variant == recovery-a05-ram ]]; then
    overlay=${3:-a05-userspace.cpio.gz}
    # Accept the donor overlay produced by check_android_userspace_qemu.py.
    # A QEMU-specific /init or any replacement of the diagnostic utilities would change the trial.
    contents=$(gzip -dc -- "$overlay" | cpio --quiet -it)
    for member in bin/check-a05-userspace system/bin/bootstrap/linker64 system/bin/init system/bin/toybox; do
        grep -Fxq "$member" <<<"$contents" || { echo "Missing $member in donor overlay" >&2; exit 1; }
    done
    if grep -Evq '^(bin|bin/check-a05-userspace|system(/[^./][^/]*)*)$' <<<"$contents"; then
        echo "Unexpected path in donor overlay" >&2
        exit 1
    fi
    cat >>ramdisk.filelist <<'EOF'
file /a05-ram-probe /dev/null 0444 0 0
file /bin/run-a05-ram-probe run-a05-ram-probe.sh 0755 0 0
EOF
fi
if [[ $variant == recovery-ufs-on-request ]]; then
    printf 'file /ufs-on-request /dev/null 0444 0 0\n' >>ramdisk.filelist
fi
if [[ $variant == recovery-ufs-module || $variant == recovery-ufs-on-request ]]; then
    test -s ufs-module/ufs-mediatek.ko
    cat >>ramdisk.filelist <<'EOF'
dir /lib 0755 0 0
dir /lib/modules 0755 0 0
file /lib/modules/ufs-mediatek.ko ufs-module/ufs-mediatek.ko 0444 0 0
EOF
fi

./gen_init_cpio -t 0 ramdisk.filelist | gzip -n >"$ramdisk.partial"
if [[ $variant == recovery-a05-ram ]]; then
    cat -- "$overlay" >>"$ramdisk.partial"
fi
gzip -t "$ramdisk.partial"
mv -f -- "$ramdisk.partial" "$ramdisk"
