"""Recover the embedded Linux configuration and version from the Galaxy A05 AP."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tarfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ap", type=Path)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    with tarfile.open(args.ap, "r:") as archive:
        entry = archive.extractfile("boot.img.lz4")
        with entry:
            compressed_boot = entry.read()
    boot = subprocess.run(
        ["lz4", "-dq", "-", "-"], input=compressed_boot, check=True, capture_output=True
    ).stdout
    if boot[:8] != b"ANDROID!" or struct.unpack_from("<I", boot, 40)[0] != 4:
        raise ValueError("Expected A05 Android boot header v4")
    length = struct.unpack_from("<I", boot, 8)[0]
    packed_kernel = boot[4096:4096 + length]
    if len(packed_kernel) != length:
        raise ValueError("Truncated kernel")
    kernel = gzip.decompress(packed_kernel)
    if kernel[56:60] != b"ARM\x64":
        raise ValueError("Unexpected arm64 Image header")
    version = re.search(rb"Linux version ([^\x00\n]+)", kernel)
    if version is None:
        raise ValueError("No Linux version banner")
    start = kernel.find(b"IKCFG_ST\x1f\x8b")
    if start < 0:
        raise ValueError("Kernel has no embedded IKCONFIG")
    start += len(b"IKCFG_ST")
    end = kernel.index(b"IKCFG_ED", start)
    config = gzip.decompress(kernel[start:end])
    if not config.startswith(b"#\n# Automatically generated file;"):
        raise ValueError("Unrecognized embedded kernel configuration")
    args.directory.mkdir(parents=True, exist_ok=True)
    (args.directory / "a05-stock.config").write_bytes(config)
    evidence = {"archive": args.ap.name, "boot_header": 4,
                "linux_version": version.group(1).decode("ascii"),
                "kernel_sha256": hashlib.sha256(kernel).hexdigest(),
                "kernel_config_sha256": hashlib.sha256(config).hexdigest()}
    (args.directory / "a05-stock-kernel.json").write_text(
        json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
    )
    print(evidence["linux_version"])
    print(f"Extracted and validated {len(config)} bytes of stock configuration")


if __name__ == "__main__":
    main()
