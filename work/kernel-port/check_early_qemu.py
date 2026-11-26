"""Exercise the arm64 image and initramfs on a generic QEMU machine."""

import argparse
from pathlib import Path
import re
import selectors
import subprocess
import time

WORK = Path(__file__).resolve().parent
IMAGE = Path("/var/tmp/codex-cph2205-build-6.6.89/arch/arm64/boot/Image")
PROMPT_PROOF = re.compile(rb"(?m)^CPH2205 shell OK\r?$")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ramdisk", type=Path, default=WORK / "ramdisk.cpio.gz")
    parser.add_argument("--dtb", type=Path, help="Override the QEMU virt device tree")
    parser.add_argument("--boot-arg", action="append", default=[])
    parser.add_argument("--command", help="Run a guest command and require a successful exit")
    parser.add_argument("--log", type=Path, default=WORK / "qemu-early.log")
    args = parser.parse_args()
    ramdisk = args.ramdisk.resolve()
    if not IMAGE.is_file() or not ramdisk.is_file():
        raise FileNotFoundError("The kernel Image and ramdisk must be built first")
    cmdline = " ".join(["console=ttyAMA0,115200", "rdinit=/init", "cph2205.console=ttyAMA0",
                        *args.boot_arg])
    command = [
        "qemu-system-aarch64", "-machine", "virt,gic-version=3", "-cpu", "cortex-a57",
        "-smp", "2", "-m", "1024", "-nodefaults", "-display", "none",
        "-monitor", "none", "-serial", "stdio", "-no-reboot",
        "-kernel", str(IMAGE), "-initrd", str(ramdisk),
        "-append", cmdline,
    ]
    if args.dtb:
        command += ["-dtb", str(args.dtb.resolve())]
    captured = bytearray()
    sent = False
    shell_check = "printf 'CPH2205 shell %s\\n' OK"
    if args.command:
        shell_check = f"( {args.command} ) && {shell_check}"
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, bufsize=0) as guest:
        try:
            with selectors.DefaultSelector() as watch:
                watch.register(guest.stdout, selectors.EVENT_READ)
                deadline = time.monotonic() + 65
                while time.monotonic() < deadline:
                    if not watch.select(timeout=max(0, deadline - time.monotonic())):
                        break
                    data = guest.stdout.read(65536)
                    if not data:
                        break
                    captured.extend(data)
                    if not sent and b"CPH2205 initramfs online" in captured:
                        guest.stdin.write((shell_check + "\r").encode())
                        sent = True
                    if sent and PROMPT_PROOF.search(captured):
                        break
        finally:
            if guest.poll() is None:
                guest.terminate()
                guest.wait(timeout=10)
            args.log.write_bytes(captured)
    if b"6.6.89-cph2205-lab" not in captured or not PROMPT_PROOF.search(captured):
        raise RuntimeError("The Linux release and interactive shell were not both verified")
    print("Verified Linux 6.6.89 and a working initramfs shell on QEMU virt")


if __name__ == "__main__":
    main()
