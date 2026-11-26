"""Replace one regular file in a streamed PAX archive, preserving its metadata."""

import argparse
import io
from pathlib import Path, PurePosixPath
import sys
import tarfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("member", type=PurePosixPath)
    parser.add_argument("replacement", type=Path)
    args = parser.parse_args()
    replacement = args.replacement.read_bytes()
    found = 0
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|*") as source:
        with tarfile.open(fileobj=sys.stdout.buffer, mode="w|", format=tarfile.PAX_FORMAT) as target:
            for member in source:
                stream = source.extractfile(member) if member.isreg() else None
                if PurePosixPath(member.name) == args.member:
                    if not member.isreg() or found:
                        raise ValueError(f"Expected one regular file at {args.member}")
                    if "SCHILY.xattr.security.selinux" not in member.pax_headers:
                        raise ValueError(f"Missing original SELinux label at {args.member}")
                    found += 1
                    member.size = len(replacement)
                    member.pax_headers.pop("size", None)
                    stream = io.BytesIO(replacement)
                target.addfile(member, stream)
    if found != 1:
        raise ValueError(f"Archive missing {args.member}")


if __name__ == "__main__":
    main()
