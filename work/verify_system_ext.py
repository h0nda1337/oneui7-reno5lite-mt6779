"""Check that repacking system_ext changed only the selected mapping's contents."""

import hashlib
import os
from pathlib import Path
import stat

ORIGINAL = Path("/tmp/codex-oneui-2205/a05/system_ext")
REPACKED = Path("/tmp/codex-oneui-2205/candidate/system_ext")
MAPPING = Path("etc/selinux/mapping/31.0.cil")
WORK = Path(__file__).parent


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def metadata(path):
    info = path.lstat()
    xattrs = {
        name: os.getxattr(path, name, follow_symlinks=False)
        for name in os.listxattr(path, follow_symlinks=False)
    }
    return info.st_mode, info.st_uid, info.st_gid, xattrs


def paths(base):
    return {Path(".")} | {path.relative_to(base) for path in base.rglob("*")}


def main():
    if not ORIGINAL.is_mount() or not REPACKED.is_mount():
        raise ValueError("Mount the original and candidate first")
    names = paths(ORIGINAL)
    if names != paths(REPACKED):
        raise ValueError("Filesystem entries changed")
    changes = []
    for name in sorted(names):
        source = ORIGINAL / name
        target = REPACKED / name
        source_meta = metadata(source)
        if source_meta != metadata(target):
            raise ValueError(f"Permissions, ownership or extended attributes changed at {name}")
        mode = source_meta[0]
        if stat.S_ISREG(mode) and digest(source) != digest(target):
            changes.append(name)
        if stat.S_ISLNK(mode) and os.readlink(source) != os.readlink(target):
            raise ValueError(f"Symbolic link changed at {name}")
    if changes != [MAPPING]:
        raise ValueError(f"Unexpected content changes: {changes}")
    if digest(REPACKED / MAPPING) != digest(WORK / "policy/a05-filtered-map/31.0.cil"):
        raise ValueError("Repacked mapping differs from the compiled mapping")
    print(f"Verified {len(names)} entries and checked ownership, modes, labels and contents")


if __name__ == "__main__":
    main()
