"""Exercise the A05 first-stage AVB mount path with three read-only OEM partitions.

Pass the extracted boot-image directory (init_boot.img, vbmeta.img and
vbmeta_system.img), the raw EROFS system.img and a directory containing the four
signed virtio modules from the kernel build. This deliberately small GPT does
not include the Samsung prism chain partition. No userdata disk is attached.
"""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from check_a05_first_stage_qemu import CMDLINE, FSTAB, WORK, decompress
from check_early_qemu import IMAGE


MODULES = ("virtio_pci_legacy_dev", "virtio_pci_modern_dev", "virtio_pci", "virtio_blk")
VM_FSTAB = """\
/dev/block/by-name/system /system erofs ro wait,avb=vbmeta_system,first_stage_mount,avb_keys=/avb/q-gsi.avbpubkey:/avb/r-gsi.avbpubkey:/avb/s-gsi.avbpubkey
/dev/block/by-name/vbmeta /vbmeta emmc defaults first_stage_mount
/dev/block/by-name/vbmeta_system /vbmeta_system emmc defaults first_stage_mount,avb=vbmeta
"""


def make_ramdisk(scratch, boot_images, module_dir):
    unpacked = scratch / "init_boot"
    subprocess.run([sys.executable, WORK / "boottools/unpack_bootimg.py", "--boot_img",
                    boot_images / "init_boot.img", "--out", unpacked], check=True,
                   stdout=subprocess.DEVNULL)
    tree = scratch / "overlay"
    fstab = tree / FSTAB
    fstab.parent.mkdir(parents=True)
    fstab.write_text(VM_FSTAB)
    modules = tree / "lib/modules"
    modules.mkdir(parents=True)
    for name in MODULES:
        shutil.copyfile(module_dir / (name + ".ko"), modules / (name + ".ko"))
    (modules / "modules.load").write_text("".join(name + ".ko\n" for name in MODULES))
    (modules / "modules.dep").write_text(
        "virtio_pci_legacy_dev.ko:\nvirtio_pci_modern_dev.ko:\n"
        "virtio_pci.ko: virtio_pci_legacy_dev.ko virtio_pci_modern_dev.ko\nvirtio_blk.ko:\n")
    for name in ("modules.alias", "modules.softdep", "modules.options"):
        (modules / name).touch()
    paths = "".join(f"{p.relative_to(tree)}\n" for p in sorted(tree.rglob("*")))
    archive = subprocess.check_output(["cpio", "--quiet", "-o", "-H", "newc", "--owner", "0:0"],
                                      cwd=tree, input=paths.encode())
    result = scratch / "probe.cpio"
    result.write_bytes(decompress((unpacked / "ramdisk").read_bytes()) + archive)
    return result


def make_read_only_disk(scratch, system, boot_images):
    # A flat extent exposes the donor bytes without copying a multi-GiB system.
    # All partition tables and padding reside in private scratch files.
    if not system.is_file() or system.stat().st_size % 512:
        raise ValueError("Expected a sector-aligned, raw system.img")
    with system.open("rb") as image:
        image.seek(1024)
        if image.read(4) != bytes.fromhex("e2e1f5e0"):
            raise ValueError("Expected an EROFS superblock in system.img")
    (scratch / "system.raw").symlink_to(system.resolve())
    for name in ("vbmeta", "vbmeta_system"):
        source = (boot_images / (name + ".img")).read_bytes()
        if not source.startswith(b"AVB0"):
            raise ValueError(f"Expected a standalone OEM AVB image for {name}")
        (scratch / (name + ".raw")).write_bytes(source + bytes(-len(source) % 512))

    offset = 2048
    extents = ['RW 2048 FLAT "gpt.sparse" 0']
    layout = ["label: gpt"]
    for name in ("system", "vbmeta", "vbmeta_system"):
        size = (scratch / (name + ".raw")).stat().st_size // 512
        layout.append(f"start={offset}, size={size}, type=L, name={name}")
        extents.append(f'RW {size} FLAT "{name}.raw" 0')
        offset += size
        padding = -offset % 2048
        if padding:
            extents.append(f'RW {padding} FLAT "gpt.sparse" {offset}')
            offset += padding
    extents.append(f'RW 2048 FLAT "gpt.sparse" {offset}')
    gpt = scratch / "gpt.sparse"
    with gpt.open("wb") as image:
        image.truncate((offset + 2048) * 512)
    subprocess.run(["sfdisk", "--no-reread", "--no-tell-kernel", gpt],
                   input="\n".join(layout) + "\n", text=True, check=True, stdout=subprocess.DEVNULL)
    info = json.loads(subprocess.check_output(["sfdisk", "--json", gpt]))
    system_uuid = info["partitiontable"]["partitions"][0]["uuid"].lower()
    disk = scratch / "disk.vmdk"
    disk.write_text('# Disk DescriptorFile\nversion=1\nCID=12345678\nparentCID=ffffffff\n'
                    'createType="twoGbMaxExtentFlat"\n\n# Extent description\n' +
                    "\n".join(extents) + '\n\n#DDB\nddb.adapterType = "lsilogic"\n')
    disk_info = json.loads(subprocess.check_output(["qemu-img", "info", "--output=json", disk]))
    if disk_info["virtual-size"] != gpt.stat().st_size:
        raise ValueError("QEMU interpreted the extent table with an unexpected disk size")
    return disk, system_uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boot-images", type=Path, required=True)
    parser.add_argument("--system", type=Path, required=True)
    parser.add_argument("--virtio-modules", type=Path, required=True)
    parser.add_argument("--kernel", type=Path, default=IMAGE)
    parser.add_argument("--log", type=Path, required=True, help="New UART capture file")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cph2205-virtio-mount-") as tmp:
        scratch = Path(tmp)
        disk, uuid = make_read_only_disk(scratch, args.system, args.boot_images)
        ramdisk = make_ramdisk(scratch, args.boot_images, args.virtio_modules)
        command = ["qemu-system-aarch64", "-machine", "virt,gic-version=3", "-cpu", "cortex-a55",
                   "-smp", "2", "-m", "1024", "-nodefaults", "-display", "none", "-monitor", "none",
                   "-nic", "none", "-serial", "stdio", "-no-reboot", "-kernel", args.kernel,
                   "-initrd", ramdisk, "-drive", f"file={disk},format=vmdk,if=none,readonly=on,id=donor",
                   "-device", "virtio-blk-pci,drive=donor,addr=0x4", "-append",
                   CMDLINE + f" printk.devkmsg=on androidboot.boot_part_uuid={uuid}" +
                   " androidboot.boot_devices=4010000000.pcie,pci0000:00/0000:00:04.0"]
        with args.log.open("xb") as output:
            subprocess.run(command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                           timeout=60, check=True)
    log = args.log.read_text(errors="replace")
    milestones = (r"init: init first stage started!", r"vda: vda1 vda2 vda3\b",
                  r"init: Loaded 4 modules from /lib/modules",
                  r"init: \[libfs_avb\] Device path not found: /dev/block/by-name/prism\b",
                  r"init: Failed to setup verity for '/system'", r"init: Failed to mount /system")
    for pattern in milestones:
        if not re.search(pattern, log):
            raise RuntimeError(f"Missing expected UART message {pattern!r}; see {args.log}")
    print("The real A05 init reached AVB on virtio. The three-partition GPT lacks prism.")


if __name__ == "__main__":
    main()
