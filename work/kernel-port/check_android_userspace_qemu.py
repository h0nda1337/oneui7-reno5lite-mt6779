"""Verify Galaxy A05 init dependencies and run its toybox under the Reno test kernel in QEMU."""

import argparse
import gzip
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


WORK = Path(__file__).resolve().parent
INIT = "system/bin/init"
LINKER = "system/bin/bootstrap/linker64"
TOYBOX = "system/bin/toybox"
CHECK = r"""#!/bin/busybox sh
set -eu
export ANDROID_ROOT=/system
export LD_LIBRARY_PATH=/system/lib64/bootstrap:/system/lib64
echo A05_INIT_LIBRARIES_BEGIN
/system/bin/bootstrap/linker64 --list /system/bin/init
echo A05_INIT_LIBRARIES_END
release=$(/system/bin/bootstrap/linker64 /system/bin/toybox uname -r)
test "$release" = "$(/bin/busybox uname -r)"
printf 'A05_TOYBOX_KERNEL=%s\n' "$release"
"""
QEMU_INIT = """#!/bin/busybox sh
/bin/busybox mount -t proc proc /proc && /bin/busybox mount -t sysfs sysfs /sys &&
    /bin/check-a05-userspace
echo A05_CHECK_EXIT=$?
/bin/busybox poweroff -f
"""


def collect_files(root):
    """Resolve each ELF dependency from this system image's bootstrap and normal libraries."""
    files = {}
    pending = [INIT, LINKER, TOYBOX]
    direct = set()
    while pending:
        name = pending.pop()
        if name in files:
            continue
        path = root / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError(f"Expected a regular file in the system image at {name}")
        files[name] = path
        info = subprocess.check_output(["readelf", "-dW", path], text=True)
        for library in re.findall(r"Shared library: \[([^\]]+)\]", info):
            if not re.fullmatch(r"[A-Za-z0-9_+.-]+\.so(?:\.[A-Za-z0-9_.-]+)?", library):
                raise ValueError(f"Unsupported DT_NEEDED name in {name}: {library}")
            if name == INIT:
                direct.add(library)
            for directory in ("system/lib64/bootstrap", "system/lib64"):
                relative = f"{directory}/{library}"
                candidate = root / relative
                if not candidate.is_symlink() and candidate.is_file():
                    pending.append(relative)
                    break
            else:
                raise ValueError(f"Missing {library} required by {name}")
    if not direct:
        raise ValueError("Expected a dynamically linked Android init")
    return files, direct


def make_archive(files, directory, builder):
    directory.mkdir()
    parents = {parent for name in files for parent in Path(name).parents if parent != Path(".")}
    entries = [f"dir /{parent} 0755 0 0" for parent in sorted(parents)]
    for number, (name, source) in enumerate(sorted(files.items())):
        # gen_init_cpio reads whitespace-separated paths, including its local input path.
        alias = f"source-{number}"
        local = directory / alias
        shutil.copyfile(source, local)
        os.utime(local, (0, 0))
        mode = "0644" if name.startswith("system/lib64/") else "0755"
        entries.append(f"file /{name} {alias} {mode} 0 0")
    (directory / "listing").write_text("\n".join(entries) + "\n")
    archive = subprocess.check_output([builder, "-t", "0", "listing"], cwd=directory)
    return gzip.compress(archive, mtime=0)


def check_result(output, direct):
    lines = output.decode("utf-8", "replace").splitlines()
    if "A05_INIT_LIBRARIES_BEGIN" not in lines or "A05_INIT_LIBRARIES_END" not in lines:
        raise RuntimeError("The Samsung linker did not complete its init dependency list")
    entries = lines[lines.index("A05_INIT_LIBRARIES_BEGIN") + 1:lines.index("A05_INIT_LIBRARIES_END")]
    resolved = {m[1] for line in entries if (m := re.match(r"\s*(\S+) => /system/lib64/", line))}
    if direct - resolved:
        raise RuntimeError(f"Missing init dependencies in linker output: {sorted(direct - resolved)}")
    releases = [line.partition("=")[2] for line in lines if line.startswith("A05_TOYBOX_KERNEL=")]
    if len(releases) != 1 or not releases[0].startswith("6.6.89-cph2205-lab") or "A05_CHECK_EXIT=0" not in lines:
        raise RuntimeError("Samsung toybox did not return the running Reno kernel release")
    return releases[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system-root", required=True, type=Path,
                        help="Mounted or extracted root of the donor system.img, containing system/bin/init")
    parser.add_argument("--kernel", required=True, type=Path, help="Built arm64 Linux Image")
    parser.add_argument("--ramdisk", required=True, type=Path, help="Base diagnostic initramfs with /bin/busybox")
    parser.add_argument("--gen-init-cpio", type=Path, default=WORK / "gen_init_cpio",
                        help="Linux gen_init_cpio binary (default: next to this script)")
    parser.add_argument("--log", type=Path, help="Save the QEMU serial transcript")
    parser.add_argument("--overlay", type=Path,
                        help="Export the verified gzip CPIO with donor binaries and /bin/check-a05-userspace for local use")
    args = parser.parse_args()
    outputs = [p.resolve() for p in (args.log, args.overlay) if p]
    inputs = {p.resolve() for p in (args.kernel, args.ramdisk, args.gen_init_cpio)}
    root = args.system_root.resolve(strict=True)
    if len(outputs) != len(set(outputs)) or any(p in inputs or p.is_relative_to(root) for p in outputs):
        parser.error("Choose distinct output paths outside the donor tree and separate from the inputs")
    files, direct = collect_files(root)
    with tempfile.TemporaryDirectory(prefix="cph2205-android-") as directory:
        tmp = Path(directory)
        check = tmp / "check"
        check.write_text(CHECK)
        files["bin/check-a05-userspace"] = check
        builder = args.gen_init_cpio.resolve(strict=True)
        overlay = make_archive(files, tmp / "donor", builder)
        init = tmp / "qemu-init"
        init.write_text(QEMU_INIT)
        wrapper = make_archive({"a05-qemu-init": init}, tmp / "wrapper", builder)
        ramdisk = tmp / "initramfs"
        with ramdisk.open("wb") as out, args.ramdisk.open("rb") as base:
            shutil.copyfileobj(base, out)
            out.write(overlay)
            out.write(wrapper)
        command = ["qemu-system-aarch64", "-machine", "virt,gic-version=3", "-cpu", "cortex-a55",
                   "-smp", "2", "-m", "1024", "-nodefaults", "-display", "none", "-monitor", "none",
                   "-serial", "stdio", "-no-reboot", "-kernel", args.kernel, "-initrd", ramdisk,
                   "-append", "console=ttyAMA0,115200 rdinit=/a05-qemu-init"]
        try:
            guest = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, timeout=45)
        except subprocess.TimeoutExpired as exc:
            if args.log:
                args.log.write_bytes(exc.stdout or b"")
            raise RuntimeError("QEMU did not finish the userspace check in 45 seconds") from exc
        if args.log:
            args.log.write_bytes(guest.stdout)
        guest.check_returncode()
        release = check_result(guest.stdout, direct)
        if args.overlay:
            args.overlay.write_bytes(overlay)
    print(f"Cortex-A55: Samsung's linker resolved {len(direct)} direct init dependencies; toybox reported {release}")


if __name__ == "__main__":
    main()
