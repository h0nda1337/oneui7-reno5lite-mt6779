"""Build and check two Android boot v2 images for temporary CPH2205 diagnostics."""

import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import struct
import subprocess
import sys

from extract_reno_tree import boot_dtb_table, unpack_table


WORK = Path(__file__).resolve().parent
TOOLS = WORK / "boottools"
OUT = WORK / "boot-diagnostics"
STOCK = Path(os.environ.get("RENO_FIRMWARE", WORK.parent / "inputs/reno")) / "boot.img"
IMAGE = Path("/var/tmp/codex-cph2205-build-6.6.89/arch/arm64/boot/Image")
DIAG_FLAGS = ["rdinit=/init", "cph2205.console=ttyS0", "cph2205.usb=1"]


def unpack(image, directory, output_format="mkbootimg"):
    result = subprocess.run(
        [sys.executable, str(TOOLS / "unpack_bootimg.py"), "--boot_img", str(image),
         "--out", str(directory), f"--format={output_format}"],
        check=True, capture_output=True, text=True,
    )
    return shlex.split(result.stdout)


def replace_arg(args, option, value):
    if args.count(option) != 1:
        raise ValueError(f"Expected exactly one argument {option}")
    args[args.index(option) + 1] = str(value)


def pack(args, result):
    subprocess.run(
        [sys.executable, str(TOOLS / "mkbootimg.py"), *args, "--output", str(result)],
        check=True,
    )


def replace_vendor_tree(stock_table, next_fdt):
    [(row, original_fdt)] = unpack_table(stock_table)
    start = row["offset"]
    if len(stock_table) != start + len(original_fdt):
        raise ValueError("Unexpected padding after the stock DTB")
    prefix = bytearray(stock_table[:start])
    _, _, _, _, _, entry_offset, _, _ = struct.unpack_from(">8I", prefix)
    struct.pack_into(">I", prefix, 4, start + len(next_fdt))
    struct.pack_into(">I", prefix, entry_offset, len(next_fdt))
    updated = bytes(prefix) + next_fdt
    [(new_row, check_fdt)] = unpack_table(updated)
    if new_row != row or check_fdt != next_fdt:
        raise ValueError("Vendor DTB table did not round-trip")
    return updated


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    original = STOCK.read_bytes()
    stock_args = unpack(STOCK, OUT / "stock-parts")
    patch_level = stock_args[stock_args.index("--os_patch_level") + 1]
    if re.fullmatch(r"\d{4}-\d{2}", patch_level):
        # The old v2 packer requires a day even though the header encodes only year/month.
        replace_arg(stock_args, "--os_patch_level", patch_level + "-01")
    control_reference = OUT / "reference-repack.img"
    pack(stock_args, control_reference)
    reference = control_reference.read_bytes()
    if reference != original[:len(reference)]:
        raise ValueError("AOSP pack tools did not reproduce the original unsigned boot payload")
    print(f"Reproduced stock unsigned boot payload byte for byte ({len(reference):,} bytes)")

    v2_header = struct.unpack_from("<9I", original, 8)
    _, kernel_address, _, ramdisk_address, _, _, _, _, version = v2_header
    linux = IMAGE.read_bytes()
    if version != 2 or linux[56:60] != b"ARM\x64":
        raise ValueError("Expected Android v2 and a valid arm64 Image")
    image_size = struct.unpack_from("<Q", linux, 16)[0]
    if kernel_address + max(image_size, len(linux)) >= ramdisk_address:
        raise ValueError("Expanded kernel overlaps the stock ramdisk load address")
    packed_linux = OUT / "Image.gz"
    packed_linux.write_bytes(gzip.compress(linux, compresslevel=6, mtime=0))
    ramdisk = WORK / "ramdisk.cpio.gz"
    if not gzip.decompress(ramdisk.read_bytes()).startswith(b"070701"):
        raise ValueError("Expected a gzip compressed newc initramfs")

    evidence = {}
    for label, kernel, dtb in (
        ("stock-kernel-control", OUT / "stock-parts/kernel", "reno-stock-diag.dtb"),
        ("linux-6.6", packed_linux, "reno-6.6-diag.dtb"),
    ):
        table = OUT / f"{label}.dtb.table"
        table.write_bytes(replace_vendor_tree(boot_dtb_table(original), (WORK / "reno-dt" / dtb).read_bytes()))
        args = stock_args.copy()
        replace_arg(args, "--kernel", kernel)
        replace_arg(args, "--ramdisk", ramdisk)
        replace_arg(args, "--dtb", table)
        cmdline = " ".join([args[args.index("--cmdline") + 1], *DIAG_FLAGS])
        replace_arg(args, "--cmdline", cmdline)
        output = OUT / f"CPH2205-{label}-ram.img"
        pack(args, output)
        data = output.read_bytes()
        rebuilt = OUT / f"{label}-parts"
        readback_args = unpack(output, rebuilt)
        if len(data) >= len(original):
            raise ValueError("Experimental boot image exceeds the stock boot partition")
        for part, source in (("kernel", kernel), ("ramdisk", ramdisk), ("dtb", table)):
            if (rebuilt / part).read_bytes() != source.read_bytes():
                raise ValueError(f"{label} {part} did not reproduce on unpack")
        for option in ("--header_version", "--pagesize", "--kernel_offset", "--ramdisk_offset",
                       "--tags_offset", "--dtb_offset", "--cmdline"):
            if readback_args[readback_args.index(option) + 1] != args[args.index(option) + 1]:
                raise ValueError(f"{label} did not preserve the expected {option}")
        evidence[label] = {"file": output.name, "size": len(data), "sha256": sha256(data)}
        print(f"{output.name}: {len(data):,} bytes, SHA-256 {sha256(data)}")
    (OUT / "manifest.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
