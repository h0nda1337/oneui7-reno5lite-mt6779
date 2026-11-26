"""Exercise the A05 first-stage AVB mount path with read-only OEM partitions.

Pass the extracted boot-image directory (init_boot.img, vbmeta.img and
vbmeta_system.img), the raw EROFS system.img and a directory containing the four
signed virtio modules from the kernel build. Supply raw prism.img and optics.img
from the matching Samsung CSC with --csc-images to extend the control GPT.
Use --android-partitions for the other seven logical images from the donor.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time

from check_a05_first_stage_qemu import CMDLINE, FSTAB, WORK, decompress
from check_early_qemu import IMAGE


MODULES = ("virtio_pci_legacy_dev", "virtio_pci_modern_dev", "virtio_pci", "virtio_blk")
VM_FSTAB = """\
/dev/block/by-name/system /system erofs ro wait,avb=vbmeta_system,first_stage_mount,avb_keys=/avb/q-gsi.avbpubkey:/avb/r-gsi.avbpubkey:/avb/s-gsi.avbpubkey
/dev/block/by-name/vbmeta /vbmeta emmc defaults first_stage_mount
/dev/block/by-name/vbmeta_system /vbmeta_system emmc defaults first_stage_mount,avb=vbmeta
"""
CSC_NAMES = ("prism", "optics")
ANDROID_NAMES = ("system_ext", "vendor", "product", "odm", "vendor_dlkm", "odm_dlkm", "system_dlkm")
APEX_EXIT = r"init: Service 'apexd-bootstrap' \(pid \d+\) exited with status 0\b"
A055MUBUGDZB2_VBMETA = "da94c475e9896ed58b4125e516859ea209e90a953ec38fc995ba6893068b4915"
CSC_FSTAB = "".join(f"/dev/block/by-name/{p} /{p} ext4 ro,barrier=1 nofail,avb,first_stage_mount\n"
                    for p in CSC_NAMES)


def make_ramdisk(scratch, boot_images, module_dir, with_csc, with_android):
    unpacked = scratch / "init_boot"
    subprocess.run([sys.executable, WORK / "boottools/unpack_bootimg.py", "--boot_img",
                    boot_images / "init_boot.img", "--out", unpacked], check=True,
                   stdout=subprocess.DEVNULL)
    tree = scratch / "overlay"
    fstab = tree / FSTAB
    fstab.parent.mkdir(parents=True)
    entries = VM_FSTAB
    if with_android:
        for name in ANDROID_NAMES:
            avb = "avb=vbmeta_system" if name == "system_ext" else "avb"
            entries += f"/dev/block/by-name/{name} /{name} erofs ro wait,{avb},first_stage_mount\n"
    fstab.write_text(entries + (CSC_FSTAB if with_csc else ""))
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


def make_read_only_disk(scratch, system, boot_images, csc_images, android_partitions):
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
    names = ["system", "vbmeta", "vbmeta_system"]
    if csc_images:
        for name in CSC_NAMES:
            image = csc_images / (name + ".img")
            if not image.is_file() or image.stat().st_size < 512 or image.stat().st_size % 512:
                raise ValueError(f"Expected a sector-aligned, raw {name}.img")
            with image.open("rb") as source:
                source.seek(-64, 2)
                if source.read(4) != b"AVBf":
                    raise ValueError(f"Missing the OEM AVB footer in {image}")
            (scratch / (name + ".raw")).symlink_to(image.resolve())
        names.extend(CSC_NAMES)
    if android_partitions:
        for name in ANDROID_NAMES:
            image = android_partitions / (name + ".img")
            if not image.is_file() or image.stat().st_size % 512:
                raise ValueError(f"Expected a sector-aligned, raw {name}.img")
            with image.open("rb") as source:
                source.seek(1024)
                if source.read(4) != bytes.fromhex("e2e1f5e0"):
                    raise ValueError(f"Expected EROFS in the donor partition {name}")
            (scratch / (name + ".raw")).symlink_to(image.resolve())
        names.extend(ANDROID_NAMES)

    offset = 2048
    extents = ['RW 2048 FLAT "gpt.sparse" 0']
    layout = ["label: gpt"]
    for name in names:
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


def bootloader_measurement(scratch, boot_images):
    # Android normally receives these fields from its bootloader. Measure the
    # exact root and child vbmeta structures in the OEM chain exposed to QEMU.
    root = (boot_images / "vbmeta.img").read_bytes()
    if hashlib.sha256(root).hexdigest() != A055MUBUGDZB2_VBMETA:
        raise ValueError("Expected the A055MUBUGDZB2 factory vbmeta.img")
    avbtool = [sys.executable, WORK.parent / "tools/avbtool.py"]
    total = 0
    for name in ("vbmeta", *CSC_NAMES):
        blob = root if name == "vbmeta" else subprocess.check_output(
            [*avbtool, "extract_vbmeta_image", "--image", scratch / (name + ".raw"),
             "--output", "/dev/stdout"])
        if len(blob) < 256 or blob[:4] != b"AVB0":
            raise ValueError(f"Invalid {name} vbmeta structure")
        auth_size, aux_size = struct.unpack_from(">QQ", blob, 12)
        size = 256 + auth_size + aux_size
        if size > len(blob):
            raise ValueError(f"Incomplete {name} vbmeta structure")
        total += size
    digest = subprocess.check_output([*avbtool, "calculate_vbmeta_digest", "--hash_algorithm",
                                      "sha256", "--image", scratch / "vbmeta.raw"], text=True).strip()
    if not re.fullmatch("[0-9a-f]{64}", digest):
        raise ValueError("AVB did not return a SHA-256 digest of the OEM chain")
    return (f" androidboot.vbmeta.hash_alg=sha256 androidboot.vbmeta.size={total}"
            f" androidboot.vbmeta.digest={digest}")


def run_guest(command, log_path, stop_after_apex):
    with log_path.open("xb") as output:
        if not stop_after_apex:
            subprocess.run(command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                           timeout=60, check=True)
            return
        # This guest keeps running beyond the checkpoint. Stop only the emulator.
        with log_path.open("rb") as reader, subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT
        ) as guest:
            deadline = time.monotonic() + 75
            tail = b""
            try:
                while guest.poll() is None:
                    chunk = tail + reader.read()
                    if re.search(APEX_EXIT.encode(), chunk):
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"Timed out waiting for apexd-bootstrap; see {log_path}")
                    tail = chunk[-256:]
                    time.sleep(0.2)
            finally:
                if guest.poll() is None:
                    guest.terminate()
                    try:
                        guest.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        guest.kill()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boot-images", type=Path, required=True)
    parser.add_argument("--system", type=Path, required=True)
    parser.add_argument("--virtio-modules", type=Path, required=True)
    parser.add_argument("--csc-images", type=Path, help="Directory with raw prism.img and optics.img")
    parser.add_argument("--android-partitions", type=Path,
                        help="Directory with all seven other raw logical EROFS images; requires --csc-images")
    parser.add_argument("--kernel", type=Path, default=IMAGE)
    parser.add_argument("--log", type=Path, required=True, help="New UART capture file")
    args = parser.parse_args()
    if args.android_partitions and not args.csc_images:
        parser.error("--android-partitions requires --csc-images")
    with tempfile.TemporaryDirectory(prefix="cph2205-virtio-mount-") as tmp:
        scratch = Path(tmp)
        disk, uuid = make_read_only_disk(scratch, args.system, args.boot_images,
                                         args.csc_images, args.android_partitions)
        ramdisk = make_ramdisk(scratch, args.boot_images, args.virtio_modules,
                               bool(args.csc_images), bool(args.android_partitions))
        measurement = bootloader_measurement(scratch, args.boot_images) if args.csc_images else ""
        command = ["qemu-system-aarch64", "-machine", "virt,gic-version=3", "-cpu", "cortex-a55",
                   "-smp", "2", "-m", "1024", "-nodefaults", "-display", "none", "-monitor", "none",
                   "-nic", "none", "-serial", "stdio", "-no-reboot", "-kernel", args.kernel,
                   "-initrd", ramdisk, "-drive", f"file={disk},format=vmdk,if=none,readonly=on,id=donor",
                   "-device", "virtio-blk-pci,drive=donor,addr=0x4", "-append",
                   CMDLINE + f" printk.devkmsg=on androidboot.boot_part_uuid={uuid}" +
                   " androidboot.boot_devices=4010000000.pcie,pci0000:00/0000:00:04.0" + measurement]
        run_guest(command, args.log, bool(args.android_partitions))
    log = args.log.read_text(errors="replace")
    partition_count = 3 + (len(CSC_NAMES) if args.csc_images else 0)
    if args.android_partitions:
        partition_count += len(ANDROID_NAMES)
    partition_list = " ".join(f"vda{i}" for i in range(1, partition_count + 1))
    milestones = [r"init: init first stage started!", rf"vda: {partition_list}\r?\n",
                  r"init: Loaded 4 modules from /lib/modules"]
    if args.csc_images:
        milestones.extend([r"init: \[libfs_avb\] Returning avb_handle with status: Success",
                           r"Built verity table: '1 /dev/block/by-name/system ",
                           r"__mount return 0\(source=dm-\d+,target=/system,type=erofs,flags=1,",
                           r"init: Switching root to '/system'"])
        if args.android_partitions:
            for name in ANDROID_NAMES:
                milestones.extend([rf"Built verity table: '1 /dev/block/by-name/{name} ",
                                   rf"__mount\(source=/dev/block/dm-\d+,target=/{name},type=erofs\)=0: Success"])
        for name in CSC_NAMES:
            milestones.extend([rf"Built verity table: '1 /dev/block/by-name/{name} ",
                               rf"__mount\(source=/dev/block/dm-\d+,target=/{name},type=ext4\)=0: Success"])
        milestones.extend([r"init: Opening SELinux policy", r"init: Compiling SELinux policy"])
        if args.android_partitions:
            milestones.extend([r"init: Loading SELinux policy", r"init: init second stage started!",
                               r"init: starting service 'ueventd'", r"init: starting service 'apexd-bootstrap'",
                               r"apexd-bootstrap: Activated 3 packages\.", APEX_EXIT])
        else:
            milestones.extend([r"init: Failed to read /vendor/etc/selinux/plat_sepolicy_vers\.txt",
                               r"init: Unable to open SELinux policy"])
    else:
        milestones.extend([r"init: \[libfs_avb\] Device path not found: /dev/block/by-name/prism\b",
                           r"init: Failed to setup verity for '/system'", r"init: Failed to mount /system"])
    position = 0
    for pattern in milestones:
        match = re.compile(pattern).search(log, position)
        if not match:
            raise RuntimeError(f"Missing expected UART message {pattern!r}; see {args.log}")
        position = match.end()
    if args.android_partitions:
        print("The A05 loaded its SELinux policy, entered second-stage init and activated its three bootstrap APEX packages.")
    elif args.csc_images:
        print("The A05 init mounted verified system, prism and optics. SELinux requires vendor policy.")
    else:
        print("The real A05 init reached AVB on virtio. The three-partition GPT lacks prism.")


if __name__ == "__main__":
    main()
