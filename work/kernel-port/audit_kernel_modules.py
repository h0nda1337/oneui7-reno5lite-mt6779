"""Compare archived kernel modules with the exported symbols of the Reno build."""

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess


ROOT = Path("/tmp/codex-oneui-2205")
SOURCES = {
    "a05": ("vendor_dlkm", "system_dlkm", "vendor", "odm"),
    "reno": ("vendor", "odm"),
}
SUFFIXES = (".ko", ".ko.gz", ".ko.xz", ".ko.zst")


def command(*args):
    return subprocess.run(args, text=True, capture_output=True, check=True).stdout.strip()


def read_versions(text):
    versions = {}
    for line in text.splitlines():
        crc, symbol, *_ = line.split()
        versions[symbol] = crc
    return versions


def inventory(device, exports):
    results = []
    for partition in SOURCES[device]:
        image = ROOT / device / partition
        if not image.is_mount():
            raise ValueError(f"Expected mounted source image {image}")
        for module in sorted((image / "lib/modules").glob("*.ko*")):
            if not module.is_file() or not module.name.endswith(SUFFIXES):
                continue
            vermagic = command("modinfo", "-F", "vermagic", str(module))
            dependencies = command("modinfo", "-F", "depends", str(module))
            required = read_versions(command("modprobe", "--show-modversions", str(module)))
            common = required.keys() & exports.keys()
            mismatch = sorted(symbol for symbol in common if required[symbol] != exports[symbol])
            results.append({
                "partition": partition,
                "file": module.name,
                "vermagic": vermagic,
                "dependencies": dependencies.split(",") if dependencies else [],
                "required_versioned_symbols": len(required),
                "matching_vmlinux_exports": len(common) - len(mismatch),
                "crc_mismatches": mismatch,
                "missing_from_vmlinux_exports": sorted(required.keys() - exports.keys()),
            })
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    exports = read_versions((args.build / "vmlinux.symvers").read_text())
    release = (args.build / "include/config/kernel.release").read_text().strip()
    report = {"candidate_kernel_release": release, "vmlinux_versioned_exports": len(exports)}
    for device in SOURCES:
        modules = inventory(device, exports)
        report[device] = modules
        verifiable = sum(item["required_versioned_symbols"] != 0 for item in modules)
        incorrect = sum(bool(item["crc_mismatches"]) for item in modules)
        print(f"{device}: {len(modules)} modules; {verifiable} with symbol CRCs; "
              f"{incorrect} with a CRC mismatch against a built-in export")
        for vermagic, count in Counter(item["vermagic"] for item in modules).items():
            print(f"  {count}: {vermagic}")
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
