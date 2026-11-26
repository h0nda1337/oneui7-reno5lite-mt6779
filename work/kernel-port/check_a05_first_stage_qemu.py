"""Boot the Galaxy A05 first-stage Android init in a diskless QEMU guest.

Use the factory init_boot ramdisk and just the donor fstab from vendor_boot.
The guest runs the port kernel on QEMU virt. No Samsung modules or partitions
are supplied. Success means PID 1 reached the expected first-stage mount wait.
"""

import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile

from check_early_qemu import IMAGE, WORK


FSTAB = "first_stage_ramdisk/fstab.mt6768"
CMDLINE = ("console=ttyAMA0,115200 rdinit=/init loglevel=8 ignore_loglevel panic=-1 "
           "androidboot.hardware=mt6768 androidboot.fstab_suffix=mt6768")


def decompress(data):
    if data.startswith(b"070701"):
        return data
    result = subprocess.run([os.environ.get("LZ4", "lz4"), "-dq", "-c"],
                            input=data, stdout=subprocess.PIPE, check=True)
    return result.stdout


def ap_ramdisks(path, scratch):
    with tarfile.open(path, "r:") as archive:
        for name, part in (("init_boot", "ramdisk"), ("vendor_boot", "vendor_ramdisk00")):
            with archive.extractfile(name + ".img.lz4") as member:
                image = scratch / (name + ".img")
                image.write_bytes(decompress(member.read()))
            parts = scratch / name
            subprocess.run([sys.executable, str(WORK / "boottools/unpack_bootimg.py"),
                            "--boot_img", str(image), "--out", str(parts)],
                           check=True, stdout=subprocess.DEVNULL)
            yield (parts / part).read_bytes()


def cpio_member(archive, name):
    result = subprocess.run(["cpio", "--quiet", "-i", "--to-stdout", name],
                            input=archive, stdout=subprocess.PIPE, check=True)
    if not result.stdout:
        raise ValueError(f"Missing or empty ramdisk member {name}")
    return result.stdout


def make_control(scratch, generic, vendor):
    init = cpio_member(generic, "init")
    if len(init) < 20 or init[:6] != b"\x7fELF\x02\x01" or init[18:20] != b"\xb7\x00":
        raise ValueError("Expected an AArch64 ELF /init from init_boot")
    fstab = scratch / FSTAB
    fstab.parent.mkdir()
    fstab.write_bytes(cpio_member(vendor, FSTAB))
    files = f"{fstab.parent.name}\n{FSTAB}\n".encode()
    prefix = subprocess.run(["cpio", "--quiet", "--owner=0:0", "-o", "-H", "newc"],
                            cwd=scratch, input=files, stdout=subprocess.PIPE, check=True)
    result = scratch / "first-stage.cpio"
    result.write_bytes(prefix.stdout + generic)
    return result, hashlib.sha256(init).hexdigest(), hashlib.sha256(fstab.read_bytes()).hexdigest()


def check_uart(log):
    release = re.search(r"Linux version (6\.6\.89\S*) ", log)
    if not release:
        raise RuntimeError("UART did not identify the 6.6.89 kernel")
    pid1 = r"\[\s*T1\]\s+"
    milestones = (r"Run /init as init process", r"init: init first stage started!",
                  r"init: !@Boot: start init process",
                  r"init: Copied ramdisk prop to /second_stage_resources/",
                  r"init: Failed to create devices required for first stage mount")
    for milestone in milestones:
        if not re.search(pid1 + milestone, log):
            raise RuntimeError(f"UART did not show PID 1 milestone: {milestone}")
    missed = re.search(pid1 + r"init: [^\n]*partition\(s\) not found after polling timeout: ([^\r\n]+)", log)
    if not missed or "super" not in missed[1].split(", "):
        raise RuntimeError("Missing the expected Samsung partition discovery timeout for super")
    return release[1], missed[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--ap", type=Path, help="Factory SM-A055M AP .tar.md5")
    sources.add_argument("--init-ramdisk", type=Path, help="Extracted init_boot ramdisk (newc or lz4)")
    parser.add_argument("--vendor-ramdisk", type=Path, help="Matching vendor_boot platform ramdisk")
    parser.add_argument("--kernel", type=Path, default=IMAGE, help="Built arm64 Image")
    args = parser.parse_args()
    if bool(args.init_ramdisk) != bool(args.vendor_ramdisk):
        parser.error("--init-ramdisk and --vendor-ramdisk must be supplied together")
    with tempfile.TemporaryDirectory(prefix="cph2205-first-stage-") as temp:
        scratch = Path(temp)
        if args.ap:
            generic, vendor = ap_ramdisks(args.ap, scratch)
        else:
            generic, vendor = args.init_ramdisk.read_bytes(), args.vendor_ramdisk.read_bytes()
        ramdisk, init_hash, fstab_hash = make_control(scratch, decompress(generic), decompress(vendor))
        command = ["qemu-system-aarch64", "-machine", "virt,gic-version=3", "-cpu", "cortex-a55",
                   "-smp", "2", "-m", "1024", "-nodefaults", "-display", "none", "-monitor", "none",
                   "-nic", "none", "-serial", "stdio", "-no-reboot", "-kernel", str(args.kernel),
                   "-initrd", str(ramdisk), "-append", CMDLINE]
        guest = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=75, check=True)
        release, missing = check_uart(guest.stdout.decode("utf-8", errors="replace"))
    print(f"First-stage /init SHA-256 {init_hash}")
    print(f"Donor fstab SHA-256 {fstab_hash}")
    print(f"Verified Samsung Android first-stage init as PID 1 and mount wait on QEMU, Linux {release}")
    print(f"Diskless guest did not find: {missing}")


if __name__ == "__main__":
    main()
