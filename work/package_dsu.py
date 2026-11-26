"""Build a local multi-partition DSU ZIP and verify each member against its source."""

import hashlib
from pathlib import Path
import zipfile

from normalize_dsu_zip64 import normalize_zip64

WORK = Path(__file__).parent
PACKAGE = WORK.parent / "outputs/OneUI7-A055M-CPH2205-DSU-exp01.zip"
PARTITIONS = (
    WORK / "a05/system.img",
    WORK / "a05/product.img",
    WORK / "dsu/system_ext.img",
)
BUFFER = 4 * 1024 * 1024


def main():
    PACKAGE.parent.mkdir(exist_ok=True)
    temporary = PACKAGE.with_suffix(".partial")
    expected = {}
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for path in PARTITIONS:
            entry = zipfile.ZipInfo(path.name, (2026, 2, 5, 0, 0, 0))
            entry.file_size = path.stat().st_size
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry._compresslevel = 1
            digest = hashlib.sha256()
            with path.open("rb") as source, archive.open(entry, "w", force_zip64=True) as destination:
                while chunk := source.read(BUFFER):
                    destination.write(chunk)
                    digest.update(chunk)
            expected[entry.filename] = (entry.file_size, digest.hexdigest())
            print(f"Packed {entry.filename} ({entry.file_size:,} bytes)", flush=True)

    normalize_zip64(temporary)
    with zipfile.ZipFile(temporary, "r") as archive:
        if archive.namelist() != list(expected):
            raise ValueError("Unexpected archive contents")
        for entry in archive.infolist():
            with archive.open(entry) as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            if (entry.file_size, digest) != expected[entry.filename]:
                raise ValueError(f"Archive data does not match source for {entry.filename}")
            print(f"Verified {entry.filename} SHA-256 {digest}", flush=True)
    with temporary.open("rb") as raw_archive:
        digest = hashlib.file_digest(raw_archive, "sha256").hexdigest()
    temporary.replace(PACKAGE)
    print(f"{PACKAGE.name} ({PACKAGE.stat().st_size:,} bytes) SHA-256 {digest}", flush=True)


if __name__ == "__main__":
    main()
