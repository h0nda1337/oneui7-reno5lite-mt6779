"""Disambiguate the directory offset for Android's native ZipFile reader."""

import argparse
from pathlib import Path
import struct


def normalize_zip64(path):
    with path.open("r+b") as archive:
        legacy_offset = archive.seek(-22, 2)
        legacy = struct.unpack("<4s4H2IH", archive.read(22))
        if legacy[0] != b"PK\x05\x06" or legacy[-1] != 0:
            raise ValueError("Expected a single-volume ZIP with no comment")
        archive.seek(legacy_offset - 20)
        signature, disk, zip64_offset, disks = struct.unpack("<4sIQI", archive.read(20))
        if signature != b"PK\x06\x07" or disk != 0 or disks != 1:
            raise ValueError("Missing ZIP64 directory locator")
        archive.seek(zip64_offset)
        zip64_record = struct.unpack("<4sQ2H2I4Q", archive.read(56))
        directory_size, directory_offset = zip64_record[-2:]
        if (zip64_record[0] != b"PK\x06\x06"
                or directory_offset + directory_size != zip64_offset
                or legacy[-2] not in (directory_offset, 0xffffffff)):
            raise ValueError("Conflicting ZIP central directory records")
        archive.seek(directory_offset)
        if archive.read(4) != b"PK\x01\x02":
            raise ValueError("Invalid central directory signature")
        patch_offset = legacy_offset + 16
        if legacy[-2] != 0xffffffff:
            archive.seek(patch_offset)
            archive.write(struct.pack("<I", 0xffffffff))
        return patch_offset


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    print(normalize_zip64(args.archive))
