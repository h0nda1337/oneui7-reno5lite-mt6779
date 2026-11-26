"""Compare MediaTek components in the archived factory packages byte-for-byte."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

COMPONENTS = ("scp", "sspm", "spmfw", "gz", "tee")


def describe(data):
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "header_hex": data[:16].hex()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ap", type=Path)
    parser.add_argument("reno", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    records = []
    with tarfile.open(args.ap, "r:") as archive:
        for name in COMPONENTS:
            member = f"{name}-verified.img.lz4"
            with archive.extractfile(member) as source:
                compressed = source.read()
            donor = subprocess.run(
                ["lz4", "-dq", "-", "-"], input=compressed, capture_output=True, check=True
            ).stdout
            target = (args.reno / f"{name}.img").read_bytes()
            records.append({"component": name, "a05": describe(donor), "reno": describe(target),
                            "byte_identical": donor == target})
    args.output.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    for record in records:
        print(f"{record['component']}: A05 {record['a05']['size']:,} bytes; "
              f"Reno {record['reno']['size']:,} bytes; identical={record['byte_identical']}")


if __name__ == "__main__":
    main()
