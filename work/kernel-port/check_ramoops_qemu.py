"""Verify panic-console persistence using the same ramoops parameters as the Reno LK."""

from pathlib import Path
import re
import selectors
import subprocess
import time

from check_early_qemu import IMAGE, WORK


QEMU = ["qemu-system-aarch64", "-cpu", "cortex-a57", "-smp", "2", "-m", "1024",
        "-nodefaults", "-display", "none"]
MACHINE = "virt,gic-version=3"
PSTORE_ADDRESS = "47c90000"
PSTORE_BYTES = "e0000"
READY = re.compile(rb"(?m)^CPH2205 initramfs online\r?$")
PROOF = re.compile(rb"(?m)^CPH2205 previous panic verified\r?$")


def reserve_test_memory(dtb):
    subprocess.run([*QEMU, "-machine", f"{MACHINE},dumpdtb={dtb}"], check=True)
    memory = subprocess.check_output(["fdtget", "-t", "x", dtb, "/memory@40000000", "reg"])
    if memory.strip() != b"0 40000000 0 40000000":
        raise ValueError("Unexpected QEMU RAM layout")
    parent = "/reserved-memory"
    node = f"{parent}/test-pstore@{PSTORE_ADDRESS}"
    operations = [
        ["-p", "-c", dtb, node],
        ["-t", "x", dtb, parent, "#address-cells", "2"],
        ["-t", "x", dtb, parent, "#size-cells", "2"],
        [dtb, parent, "ranges"],
        ["-t", "x", dtb, node, "reg", "0", PSTORE_ADDRESS, "0", PSTORE_BYTES],
        [dtb, node, "no-map"],
    ]
    for operation in operations:
        subprocess.run(["fdtput", *operation], check=True)


def main():
    dtb = WORK / "qemu-virt-ramoops.dtb"
    reserve_test_memory(dtb)
    cmdline = " ".join((
        "console=ttyAMA0,115200 rdinit=/init cph2205.console=ttyAMA0 panic=-1",
        f"ramoops.mem_address=0x{PSTORE_ADDRESS} ramoops.mem_size=0x{PSTORE_BYTES}",
        "ramoops.pmsg_size=0x10000 ramoops.console_size=0x40000",
    ))
    command = [*QEMU, "-machine", MACHINE, "-monitor", "none", "-serial", "stdio",
               "-kernel", IMAGE, "-initrd", WORK / "ramdisk.cpio.gz", "-dtb", dtb,
               "-append", cmdline]
    output = bytearray()
    ramdisk_boots = 0
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, bufsize=0) as guest:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(guest.stdout, selectors.EVENT_READ)
                deadline = time.monotonic() + 50
                while selector.select(timeout=max(0, deadline - time.monotonic())):
                    block = guest.stdout.read(65536)
                    if not block:
                        break
                    output.extend(block)
                    starts = len(READY.findall(output))
                    if starts == 1 and ramdisk_boots == 0:
                        # SysRq crash is a deliberate self-test in the disposable QEMU guest.
                        guest.stdin.write(b"echo c >/proc/sysrq-trigger\r")
                        ramdisk_boots = 1
                    if starts == 2 and ramdisk_boots == 1:
                        guest.stdin.write(
                            b"mount -t pstore pstore /sys/fs/pstore && "
                            b"grep -F 'Kernel panic - not syncing: sysrq triggered crash' "
                            b"/sys/fs/pstore/console-ramoops-0 && "
                            b"printf 'CPH2205 previous panic %s\\n' verified\r"
                        )
                        ramdisk_boots = 2
                    if PROOF.search(output) or time.monotonic() >= deadline:
                        break
        finally:
            if guest.poll() is None:
                guest.terminate()
                guest.wait(timeout=10)
            (WORK / "qemu-ramoops.log").write_bytes(output)
    if ramdisk_boots != 2 or not PROOF.search(output):
        raise RuntimeError("Could not recover the first boot's panic from ramoops")
    print("Verified 6.6.89 ramoops console after a QEMU guest reset")


if __name__ == "__main__":
    main()
