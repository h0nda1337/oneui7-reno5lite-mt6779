"""Replay the Reno's captured LK framebuffer handoff on a generic QEMU guest."""

import hashlib
import os
from pathlib import Path
import subprocess
import sys

import libfdt

from check_early_qemu import WORK


CAPTURE = Path(os.environ.get("RENO_RUNTIME_DTB", WORK.parent / "inputs/reno/runtime.dtb"))
CAPTURE_SHA256 = "15b90220d0658920d37b7756d20c58cd4933190b186cb1e0e099ba18408fddf4"
FRAMEBUFFER = "mblock-16-framebuffer"
TAG = "atag,videolfb"
TEST_FB = "/sys/class/graphics/fb0"


def make_fixtures():
    data = CAPTURE.read_bytes()
    if hashlib.sha256(data).hexdigest() != CAPTURE_SHA256:
        raise ValueError("Unexpected source for the Reno LK handoff")
    reno = libfdt.Fdt(data)
    chosen = reno.path_offset("/chosen")
    reserved = reno.path_offset(f"/reserved-memory/{FRAMEBUFFER}")

    template = WORK / "qemu-lkfb-template.dtb"
    subprocess.run(["qemu-system-aarch64", "-machine", f"virt,gic-version=3,dumpdtb={template}",
                    "-cpu", "cortex-a57", "-smp", "2", "-m", "1024", "-nodefaults",
                    "-display", "none"], check=True)
    qemu = libfdt.Fdt(template.read_bytes())
    qemu.resize(len(qemu.as_bytearray()) + 4096)
    compatible = bytes(qemu.getprop(0, "compatible"))
    qemu.setprop(0, "compatible", b"mediatek,MT6779\0" + compatible)
    for prop in (TAG, "atag,videolfb-fb_base_h", "atag,videolfb-fb_base_l", "atag,videolfb-vramSize"):
        qemu.setprop(qemu.path_offset("/chosen"), prop, bytes(reno.getprop(chosen, prop)))

    qemu.add_subnode(0, "reserved-memory")
    parent = qemu.path_offset("/reserved-memory")
    qemu.setprop_u32(parent, "#address-cells", 2)
    qemu.setprop_u32(parent, "#size-cells", 2)
    qemu.setprop(parent, "ranges", b"")
    frame = qemu.add_subnode(parent, FRAMEBUFFER)
    for prop in ("compatible", "no-map", "reg"):
        qemu.setprop(frame, prop, bytes(reno.getprop(reserved, prop)))

    good = WORK / "qemu-lkfb-matching.dtb"
    qemu.pack()
    good.write_bytes(bytes(qemu.as_bytearray()))
    tag = bytearray(qemu.getprop(qemu.path_offset("/chosen"), TAG))
    # An address one page into the reservation must also be rejected.
    tag[:8] = (int.from_bytes(tag[:8], "little") + 4096).to_bytes(8, "little")
    qemu.setprop(qemu.path_offset("/chosen"), TAG, bytes(tag))
    bad = WORK / "qemu-lkfb-mismatched.dtb"
    bad.write_bytes(bytes(qemu.as_bytearray()))
    return good, bad


def boot(dtb, phase, command, enabled=True):
    args = [sys.executable, WORK / "check_early_qemu.py", "--dtb", dtb, "--command", command,
            "--log", WORK / f"qemu-lkfb-{phase}.log"]
    if enabled:
        args += ["--boot-arg", "cph2205.lkfb=1"]
    subprocess.run(args, check=True)


def main():
    valid, invalid = make_fixtures()
    last_pixel = 2399 * (4352 // 4) + 1079
    boot(valid, "accepted", f'test "$(cat {TEST_FB}/virtual_size)" = 1080,2400 && '
         f'test "$(cat {TEST_FB}/stride)" = 4352 && grep -qx simple {TEST_FB}/name && '
         f'IFS=: read -r major minor <{TEST_FB}/dev && mknod /dev/lkfb c "$major" "$minor" && '
         f'printf LKFB | dd of=/dev/lkfb bs=4 count=1 seek={last_pixel} 2>/dev/null && '
         f'test "$(dd if=/dev/lkfb bs=4 count=1 skip={last_pixel} 2>/dev/null)" = LKFB')
    boot(invalid, "rejected", f"test ! -e {TEST_FB}")
    boot(valid, "disabled", f"test ! -e {TEST_FB}", enabled=False)
    print("Verified the captured LK framebuffer geometry, pixel access, and rejection cases in QEMU")


if __name__ == "__main__":
    main()
