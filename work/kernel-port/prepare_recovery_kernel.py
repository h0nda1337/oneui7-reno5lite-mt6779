"""Prepare a host-only kernel diagnostic using the archived stock recovery as a template."""

import argparse
import gzip
import json
import os
from pathlib import Path
import re
import struct
from recovery_avb import add_footer, properties

from package_reno_boot import (
    DIAG_FLAGS, WORK, boot_dtb_table, pack, replace_arg, replace_vendor_tree, sha256, unpack,
)

STOCK = Path(os.environ.get("RENO_RECOVERY", WORK.parent / "inputs/reno/recovery.bin"))
STOCK_SHA256 = "ac989e3009148792101a764a82fca8ca6212ec0afdd84e13d3b85e87364903b1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", required=True, type=Path, help="Uncompressed arm64 Linux Image")
    parser.add_argument("--dtb", type=Path, default=WORK / "reno-dt/reno-6.6-diag.dtb",
                        help="Base DTB to include with the stock recovery overlays")
    parser.add_argument("--ramdisk", type=Path, default=WORK / "ramdisk-recovery.cpio.gz",
                        help="Diagnostic initramfs to package")
    parser.add_argument("--boot-arg", action="append", default=[], help="Extra diagnostic boot argument")
    parser.add_argument("--output", required=True, type=Path, help="New directory for this experiment")
    options = parser.parse_args()
    output = options.output.resolve()

    original = STOCK.read_bytes()
    if len(original) != 64 * 1024 * 1024 or sha256(original) != STOCK_SHA256:
        raise ValueError("The recovery backup differs from the known stock image")
    output.mkdir(parents=True)
    stock_parts = output / "stock-parts"
    args = unpack(STOCK, stock_parts)
    patch = args[args.index("--os_patch_level") + 1]
    if re.fullmatch(r"\d{4}-\d{2}", patch):
        replace_arg(args, "--os_patch_level", patch + "-01")
    reference = output / "reference-repack.img"
    pack(args, reference)
    rebuilt = reference.read_bytes()
    if rebuilt != original[:len(rebuilt)]:
        raise ValueError("Stock unsigned recovery did not round-trip")
    print(f"Reproduced stock recovery payload byte for byte ({len(rebuilt):,} bytes)")

    table = output / "linux-6.6-diagnostic.dtb.table"
    fdt = options.dtb.read_bytes()
    table.write_bytes(replace_vendor_tree(boot_dtb_table(original), fdt))
    raw_kernel = options.kernel.read_bytes()
    if raw_kernel[56:60] != b"ARM\x64":
        raise ValueError("Expected an uncompressed arm64 kernel")
    kernel_address, ramdisk_address = (struct.unpack_from("<I", original, i)[0] for i in (12, 20))
    kernel_size = struct.unpack_from("<Q", raw_kernel, 16)[0]
    if kernel_address + max(kernel_size, len(raw_kernel)) >= ramdisk_address:
        raise ValueError("Expanded kernel overlaps the archived ramdisk load address")
    kernel = output / "Image.gz"
    kernel.write_bytes(gzip.compress(raw_kernel, compresslevel=6, mtime=0))
    ramdisk = options.ramdisk.resolve()
    replace_arg(args, "--kernel", kernel)
    replace_arg(args, "--ramdisk", ramdisk)
    replace_arg(args, "--dtb", table)
    cmdline = args[args.index("--cmdline") + 1]
    diagnostic_args = [*DIAG_FLAGS, "cph2205.recovery=1", *options.boot_arg]
    replace_arg(args, "--cmdline", " ".join([cmdline, *diagnostic_args]))

    candidate = output / "recovery.img"
    pack(args, candidate)
    temporary_parts = output / "diagnostic-parts"
    readback = unpack(candidate, temporary_parts)
    if readback[readback.index("--cmdline") + 1] != args[args.index("--cmdline") + 1]:
        raise ValueError("Recovery command line did not round-trip")
    for part, source in (("kernel", kernel), ("ramdisk", ramdisk), ("dtb", table),
                         ("recovery_dtbo", stock_parts / "recovery_dtbo")):
        if (temporary_parts / part).read_bytes() != source.read_bytes():
            raise ValueError(f"Recovery {part} did not round-trip")

    retained = add_footer(candidate, original, WORK.parent / "tools/avbtool.py", len(original))
    data = candidate.read_bytes()
    if len(data) != len(original) or properties(data) != retained:
        raise ValueError("Unexpected recovery size or AVB properties")
    report = {"recovery_file": candidate.name, "sha256": sha256(data),
              "stock_recovery_sha256": STOCK_SHA256, "bytes": len(data),
              "kernel_sha256": sha256(raw_kernel), "base_dtb_sha256": sha256(fdt),
              "ramdisk_sha256": sha256(ramdisk.read_bytes()),
              "diagnostic_boot_args": diagnostic_args,
              "stock_avb_properties": retained, "oem_signature": False,
              "physical_boot_verified": False}
    (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Diagnostic recovery verified offline ({len(data):,} bytes, SHA-256 {sha256(data)})")


if __name__ == "__main__":
    main()
