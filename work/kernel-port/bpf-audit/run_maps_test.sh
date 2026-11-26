#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

llvm-objcopy-19 -O binary --only-section=.android_maps a05-tethering-test.o a05-tethering-test.maps
aarch64-linux-gnu-gcc -static -O2 -Wall -Wextra -Werror \
    -o probe_tethering_maps probe_tethering_maps.c

cat >probe.filelist <<'EOF'
file /bin/probe-tethering-maps probe_tethering_maps 0755 0 0
file /a05-tethering-test.maps a05-tethering-test.maps 0444 0 0
EOF

cp ../ramdisk.cpio.gz qemu-probe-ramdisk.cpio.gz
../gen_init_cpio -t 0 probe.filelist | gzip -n >>qemu-probe-ramdisk.cpio.gz
python3 ../check_early_qemu.py --ramdisk qemu-probe-ramdisk.cpio.gz --log qemu-maps.log \
    --command '/bin/probe-tethering-maps /a05-tethering-test.maps'
