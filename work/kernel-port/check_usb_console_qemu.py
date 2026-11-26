"""Boot the recovery ramdisk and interact with its CDC ACM shell through dummy_hcd."""

import argparse
import gzip
from pathlib import Path
import selectors
import shutil
import subprocess
import tempfile
import time

from check_early_qemu import IMAGE, WORK


BUILD = IMAGE.parents[3]
MODULES = ("drivers/usb/gadget/udc/dummy_hcd.ko", "drivers/usb/class/cdc-acm.ko")
WRAPPER = r"""#!/bin/busybox sh
set -eu
for module in /qemu-usb/*.ko; do /bin/busybox insmod "$module"; done
/bin/busybox sh /qemu-usb/check >/dev/ttyAMA0 2>&1 &
exec /init
"""
USB_HOST = r"""#!/bin/busybox sh
set -eu
export PATH=/bin
for i in $(/bin/busybox seq 1 18); do
    if [ -r /sys/class/tty/ttyACM0/dev ]; then break; fi
    /bin/busybox sleep 1
done
IFS=: read -r major minor </sys/class/tty/ttyACM0/dev
mknod /dev/qemu-acm c "$major" "$minor"
exec 3<>/dev/qemu-acm
stty -F /dev/qemu-acm raw -echo
printf 'QEMU opened the simulated host ACM port\n'
cat <&3 >/qemu-usb/wire &
exchange() {
    for attempt in $(seq 1 8); do
        # A host tty may briefly echo the gadget's login banner before stty runs.
        printf '\003\r' >&3
        sleep 0.2
        printf '%s\r' "$1" >&3
        sleep 1
        if tr -d '\r' </qemu-usb/wire | grep -Fxq "$2"; then return 0; fi
    done
    echo 'USB test received these bytes'
    head -c 1024 /qemu-usb/wire | hexdump -C
    ps
    echo 'QEMU USB exchange failed'
    return 1
}
exchange "printf '\\nR%s %s\\n' ac2205 \"\$(uname -r)\" | tee /dev/kmsg" "Rac2205 $(uname -r)"
exchange "test -f /recovery-diagnostic && ! grep -q '^ufs_mediatek ' /proc/modules && printf '\\nCPH2205 ACM %s\\n' EARLY" 'CPH2205 ACM EARLY'
echo 'QEMU received early USB shell response'
sleep 21
if [ -f /ufs-on-request ]; then
    exchange "test ! -f /dev/cph2205-ufs-request && ! grep -q '^ufs_mediatek ' /proc/modules && printf '\\nCPH2205 ACM %s\\n' WAITING" 'CPH2205 ACM WAITING'
    exchange ": > /dev/cph2205-ufs-request && printf '\\nCPH2205 ACM %s\\n' REQUESTED" 'CPH2205 ACM REQUESTED'
fi
exchange "grep -q '^ufs_mediatek ' /proc/modules && printf '\\nCPH2205 ACM %s\\n' LATE" 'CPH2205 ACM LATE'
echo 'QEMU verified both USB shell responses'
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ramdisk", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--boot-arg", action="append", default=[],
                        help="Append a diagnostic kernel argument to the QEMU boot")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cph2205-usb-") as temp:
        tmp = Path(temp)
        files = ["dir /qemu-usb 0755 0 0"]
        for guest_path, contents in (("/qemu-usb-init", WRAPPER), ("/qemu-usb/check", USB_HOST)):
            local = tmp / Path(guest_path).name
            local.write_text(contents)
            files.append(f"file {guest_path} {local} 0755 0 0")
        for module in MODULES:
            local = tmp / Path(module).name
            shutil.copyfile(BUILD / module, local)
            if not local.read_bytes().endswith(b"~Module signature appended~\n"):
                subprocess.run([BUILD/"scripts/sign-file", "sha1", BUILD/"certs/signing_key.pem",
                                BUILD/"certs/signing_key.x509", local], check=True)
            files.append(f"file /qemu-usb/{local.name} {local} 0444 0 0")
        listing = tmp/"files"
        listing.write_text("\n".join(files) + "\n")
        overlay = subprocess.check_output([WORK/"gen_init_cpio", "-t", "0", listing])
        ramdisk = tmp/"ramdisk"
        ramdisk.write_bytes(args.ramdisk.read_bytes() + gzip.compress(overlay, mtime=0))
        command = ["qemu-system-aarch64", "-machine", "virt,gic-version=3", "-cpu", "cortex-a57",
                   "-smp", "2", "-m", "1024", "-nodefaults", "-display", "none", "-monitor", "none",
                   "-serial", "stdio", "-no-reboot", "-kernel", IMAGE, "-initrd", ramdisk,
                   "-append", " ".join(["console=ttyAMA0,115200", "rdinit=/qemu-usb-init",
                                        *args.boot_arg])]
        proof = b"QEMU verified both USB shell responses"
        captured = bytearray()
        with subprocess.Popen(command, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL,
                              stderr=subprocess.STDOUT, bufsize=0) as guest:
            try:
                with selectors.DefaultSelector() as events:
                    events.register(guest.stdout, selectors.EVENT_READ)
                    deadline = time.monotonic() + 55
                    while time.monotonic() < deadline and proof not in captured and b"QEMU USB exchange failed" not in captured:
                        if not events.select(max(0, deadline - time.monotonic())):
                            break
                        data = guest.stdout.read(65536)
                        if not data:
                            break
                        captured.extend(data)
            finally:
                guest.terminate()
                guest.wait(timeout=10)
                args.log.write_bytes(captured)
        if proof not in captured:
            raise RuntimeError(f"Missing USB shell responses. See {args.log}")
        print("Verified interactive CDC ACM before and after deferred UFS module load on QEMU")


if __name__ == "__main__":
    main()
