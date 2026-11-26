"""Extract FDTs from the CPH2205 stock Android boot and DTBO images."""

import argparse
import hashlib
import json
from pathlib import Path
import struct

FDT_MAGIC = 0xD00DFEED
TABLE_MAGIC = 0xD7B7AB1E


def check_fdt(data):
    if len(data) < 40:
        raise ValueError("Truncated FDT")
    magic, size = struct.unpack_from(">II", data)
    if magic != FDT_MAGIC or size != len(data):
        raise ValueError("Bad FDT magic or length")
    return data


def unpack_table(data):
    magic, size, header_size, entry_size, count, offset, _, version = struct.unpack_from(">8I", data)
    if (magic != TABLE_MAGIC or version != 0 or header_size < 32 or entry_size < 32
            or offset < header_size or offset + entry_size * count > size or size > len(data)):
        raise ValueError("Unsupported or truncated device tree table")
    for index in range(count):
        length, start, ident, revision, *custom = struct.unpack_from(">8I", data, offset + index * entry_size)
        if start < offset + entry_size * count or start + length > size:
            raise ValueError(f"DT entry {index} points outside the payload")
        yield {"index": index, "id": ident, "revision": revision, "custom": custom,
               "offset": start}, check_fdt(data[start:start + length])


def boot_dtb_table(image):
    if image[:8] != b"ANDROID!" or struct.unpack_from("<I", image, 40)[0] != 2:
        raise ValueError("Expected an Android v2 boot image")
    kernel, _, ramdisk, _, second, _, _, page_size = struct.unpack_from("<8I", image, 8)
    recovery_dtbo, _, _, dtb_size, _ = struct.unpack_from("<IQIIQ", image, 1632)
    if page_size not in (2048, 4096, 8192, 16384):
        raise ValueError("Unsupported boot page size")
    offset = page_size
    for size in (kernel, ramdisk, second, recovery_dtbo):
        offset += (size + page_size - 1) // page_size * page_size
    if offset + dtb_size > len(image):
        raise ValueError("Truncated boot device tree section")
    return image[offset:offset + dtb_size]


def export_table(entries, output, stem):
    report = []
    for description, blob in entries:
        name = f"{stem}-{description['index']}.dtb"
        (output / name).write_bytes(blob)
        report.append({**description, "file": name, "size": len(blob),
                       "sha256": hashlib.sha256(blob).hexdigest()})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("boot", type=Path)
    parser.add_argument("dtbo", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    base = list(unpack_table(boot_dtb_table(args.boot.read_bytes())))
    overlays = list(unpack_table(args.dtbo.read_bytes()))
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"base": export_table(base, args.output, "base"),
              "overlays": export_table(overlays, args.output, "overlay")}
    (args.output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Verified and extracted {len(base)} base DTB(s) and {len(overlays)} overlay(s)")


if __name__ == "__main__":
    main()
