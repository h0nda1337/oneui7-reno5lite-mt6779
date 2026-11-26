"""Read a single-device Android super image and export selected logical partitions."""

import argparse
from bisect import bisect_right
import hashlib
import json
from pathlib import Path
import re
import struct

MIB = 1024 * 1024
SECTOR_SIZE = 512


class SuperImage:
    def __init__(self, source):
        self.source = source
        self.length = source.seek(0, 2)
        self.chunks = []
        self.starts = []
        if struct.unpack("<I", self.read_raw(0, 4))[0] == 0xED26FF3A:
            self.index_sparse_chunks()

    def read_raw(self, offset, length):
        self.source.seek(offset)
        data = self.source.read(length)
        if len(data) != length:
            raise ValueError(f"Truncated image at byte {offset}")
        return data

    def index_sparse_chunks(self):
        _, major, _, header_size, chunk_size, block_size, blocks, count, _ = (
            struct.unpack("<I4H4I", self.read_raw(0, 28))
        )
        if major != 1 or header_size < 28 or chunk_size < 12 or block_size % 4:
            raise ValueError("Unsupported sparse header")
        logical_offset = 0
        file_offset = header_size
        for _ in range(count):
            kind, _, chunk_blocks, file_size = struct.unpack(
                "<2H2I", self.read_raw(file_offset, 12)
            )
            logical_size = chunk_blocks * block_size
            payload_sizes = {0xCAC1: logical_size, 0xCAC2: 4, 0xCAC3: 0, 0xCAC4: 4}
            if kind not in payload_sizes or file_size != chunk_size + payload_sizes[kind]:
                raise ValueError(f"Invalid sparse chunk at byte {file_offset}")
            if kind == 0xCAC4 and logical_size:
                raise ValueError("Invalid CRC chunk")
            if logical_size:
                self.starts.append(logical_offset)
                self.chunks.append((logical_offset + logical_size, kind, file_offset + chunk_size))
            logical_offset += logical_size
            file_offset += file_size
        if logical_offset != blocks * block_size or file_offset != self.length:
            raise ValueError("Sparse image length does not match its headers")
        self.length = logical_offset

    def read(self, offset, length):
        if offset < 0 or offset + length > self.length:
            raise ValueError("Read outside super image")
        if not self.chunks:
            return self.read_raw(offset, length)
        result = bytearray()
        while length:
            index = bisect_right(self.starts, offset) - 1
            end, kind, payload = self.chunks[index]
            position = offset - self.starts[index]
            size = min(length, end - offset)
            if kind == 0xCAC1:
                result.extend(self.read_raw(payload + position, size))
            elif kind == 0xCAC2:
                pattern = self.read_raw(payload, 4)
                start = position % 4
                result.extend((pattern * ((start + size + 3) // 4))[start:start + size])
            else:
                result.extend(bytes(size))
            offset += size
            length -= size
        return result


def checksum_matches(data, offset):
    expected = data[offset:offset + 32]
    return hashlib.sha256(bytes(data[:offset]) + bytes(32) + bytes(data[offset + 32:])).digest() == expected


def read_metadata(image, slot):
    geometry = image.read(4096, 52)
    magic, size = struct.unpack_from("<2I", geometry)
    if magic != 0x616C4467 or size != len(geometry) or not checksum_matches(geometry, 8):
        raise ValueError("Invalid primary LP geometry")
    max_size, slots, block_size = struct.unpack_from("<3I", geometry, 40)
    if not 0 <= slot < slots:
        raise ValueError(f"Invalid slot {slot}, image contains {slots} metadata slots")
    offset = 3 * 4096 + slot * max_size
    basic_header = image.read(offset, 128)
    magic, major, minor, header_size = struct.unpack_from("<I2HI", basic_header)
    tables_size = struct.unpack_from("<I", basic_header, 44)[0]
    if magic != 0x414C5030 or major != 10 or not 128 <= header_size <= max_size - tables_size:
        raise ValueError("Invalid LP header")
    header = image.read(offset, header_size)
    tables = image.read(offset + header_size, tables_size)
    if not checksum_matches(header, 12) or hashlib.sha256(tables).digest() != header[48:80]:
        raise ValueError("Invalid LP metadata checksums")

    def table(descriptor_offset, fmt):
        start, count, entry_size = struct.unpack_from("<3I", header, descriptor_offset)
        if entry_size != struct.calcsize(fmt) or start + count * entry_size > len(tables):
            raise ValueError("Invalid LP table descriptor")
        return list(struct.iter_unpack(fmt, tables[start:start + count * entry_size]))

    def name(raw):
        value = raw.rstrip(b"\0").decode("ascii")
        if not re.fullmatch(r"[A-Za-z0-9_]+", value):
            raise ValueError("Invalid LP name")
        return value

    groups = {name(raw): capacity for raw, _, capacity in table(104, "<36sIQ")}
    devices = table(116, "<QIIQ36sI")
    if len(devices) != 1 or devices[0][3] != image.length:
        raise ValueError("Expected one complete block device")
    extents = table(92, "<QIQI")
    partitions = {}
    for raw, attributes, start, count, group in table(80, "<36s4I"):
        runs = extents[start:start + count]
        if len(runs) != count or name(raw) in partitions:
            raise ValueError("Invalid partition entry")
        partitions[name(raw)] = {
            "size": sum(sectors * SECTOR_SIZE for sectors, _, _, _ in runs),
            "attributes": attributes,
            "group": list(groups)[group],
            "extents": runs,
        }
    return {"size": image.length, "block_size": block_size, "slot": slot,
            "slots": slots, "version": f"{major}.{minor}", "groups": groups, "partitions": partitions}


def export_partition(image, partition, dest):
    temporary = dest.with_suffix(".partial")
    with temporary.open("wb") as output:
        for sectors, kind, sector, device in partition["extents"]:
            if device or kind not in (0, 1):
                raise ValueError("Unsupported LP extent")
            count = sectors * SECTOR_SIZE
            for position in range(0, count, 4 * MIB):
                size = min(count - position, 4 * MIB)
                data = image.read(sector * SECTOR_SIZE + position, size) if kind == 0 else bytes(size)
                output.write(data)
    temporary.replace(dest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--slot", type=int, default=0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--extract", nargs="+", default=[])
    args = parser.parse_args()
    if args.extract and not args.output:
        parser.error("--extract requires --output")
    with args.image.open("rb") as source:
        image = SuperImage(source)
        metadata = read_metadata(image, args.slot)
        if args.output:
            args.output.mkdir(parents=True, exist_ok=True)
        for name in args.extract:
            export_partition(image, metadata["partitions"][name], args.output / f"{name}.img")
        print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
