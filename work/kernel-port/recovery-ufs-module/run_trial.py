"""Run a verified recovery diagnostic once and restore CPH2205 factory recovery."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import time


RUN = Path(__file__).resolve().parent
LOCAL_SETTINGS = RUN.parent / "device.local.json"
settings = json.loads(LOCAL_SETTINGS.read_text(encoding="utf-8")) if LOCAL_SETTINGS.is_file() else {}
SERIAL = os.getenv("RENO_ADB_SERIAL") or settings.get("serial", "")
ADB = ["adb", "-s", SERIAL]
FASTBOOT = ["fastboot", "-s", SERIAL]
STOCK = Path(os.getenv("RENO_RECOVERY") or settings.get("stock_recovery", RUN.parents[1]/"inputs/reno/recovery.bin"))
STOCK_HASH = "ac989e3009148792101a764a82fca8ca6212ec0afdd84e13d3b85e87364903b1"
IMAGE_HASH = "3e72cc8d0f2107acb2f15834ec4131eebedba24930058ebd208246edb713bdf5"
PROPERTIES = ("ro.product.model", "ro.build.display.id", "sys.boot_completed",
              "ro.boot.flash.locked", "sys.boot.reason", "sys.boot.reason.last", "ro.boot.bootreason")


def run(argv, check=True, timeout=10, log=None):
    result = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout)
    output = (result.stdout + result.stderr).strip()
    if log:
        with (RUN / log).open("a", encoding="utf-8") as out:
            print(output, file=out)
    if check and result.returncode:
        raise RuntimeError(f"{argv[0]} {argv[3]} failed: {output}")
    return output


def note(message):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def check_image(path, digest):
    if path.stat().st_size != 67108864 or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise RuntimeError(f"Image identity mismatch for {path}")


def diagnostic_release(image):
    with image.open("rb") as stream:
        header = stream.read(44)
        if header[:8] != b"ANDROID!" or struct.unpack_from("<I", header, 40)[0] != 2:
            raise ValueError("Expected Android recovery header v2")
        size = struct.unpack_from("<I", header, 8)[0]
        stream.seek(struct.unpack_from("<I", header, 36)[0])
        kernel = gzip.decompress(stream.read(size))
    release = re.search(rb"Linux version (6\.6\.89-cph2205-lab[\w.+-]*) ", kernel)
    if not release:
        raise ValueError("Diagnostic Linux release not found in the verified recovery")
    return release[1].decode("ascii")


def coloros(log=None):
    if run([*ADB, "get-state"], check=False) != "device":
        return False
    command = " && ".join([*(f"getprop {name}" for name in PROPERTIES), "uname -r"])
    lines = run([*ADB, "shell", command], check=False).splitlines()
    if len(lines) != 8 or lines[:4] != ["CPH2205", "CPH2205_11_F.72", "1", "0"] or lines[-1] != "4.19.191+":
        return False
    if log:
        (RUN / log).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def bootloader_connected():
    devices = run(["fastboot", "devices"], check=False)
    return re.search(rf"(?m)^{re.escape(SERIAL)}\s+fastboot\s*$", devices) is not None


def wait_for(probe, seconds):
    limit = time.monotonic() + seconds
    while time.monotonic() < limit:
        try:
            if probe():
                return True
        except subprocess.TimeoutExpired:
            pass
        time.sleep(1)
    return False


def preflight(stage):
    if not wait_for(bootloader_connected, 45):
        raise RuntimeError(f"Reno bootloader unavailable for {stage}")
    expected = {"product": "k79v1_64", "is-userspace": "no", "unlocked": "yes",
                "partition-size:recovery": "(?:0x)?4000000", "max-download-size": "(?:0x)?8000000"}
    for key, value in expected.items():
        output = run([*FASTBOOT, "getvar", key], log=f"{stage}-preflight.log")
        if not re.search(rf"(?m)^{re.escape(key)}:\s*{value}\s*$", output):
            raise RuntimeError(f"Unexpected {key} for {stage}: {output}")


def write_recovery(path, stage):
    output = run([*FASTBOOT, "flash", "recovery", str(path)], timeout=90, log=f"{stage}-fastboot.log")
    if not re.search(r"Writing 'recovery'\s+OKAY", output):
        raise RuntimeError(f"Missing recovery write acknowledgement for {stage}")


def capture_previous_boot():
    # Read only bring-up markers before the return to the bootloader rotates pstore.
    command = (
        "for f in /sys/fs/pstore/console-ramoops* /sys/fs/pstore/dmesg-ramoops*; do "
        "if [ -r \"$f\" ]; then echo \"$f\"; "
        "grep -aE 'Linux version|CPH2205 (initramfs|UFS|loading|received)|"
        "11201000\\.usb0|11270000\\.ufshci|Kernel panic|watchdog:' \"$f\" | tail -n 80; fi; done"
    )
    try:
        output = run([*ADB, "shell", command], timeout=4)
        (RUN / "first-return-pstore.log").write_text(
            (output or "No readable pstore record") + "\n", encoding="utf-8")
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        note(f"Previous boot log unavailable ({type(error).__name__})")


def restore():
    note("Waiting for the known Reno in ColorOS or bootloader to restore recovery")
    if not wait_for(lambda: bootloader_connected() or coloros("post-diagnostic-adb.txt"), 300):
        raise RuntimeError("Factory recovery still needs restoring. Reno is not reachable")
    if not bootloader_connected():
        capture_previous_boot()
        run([*ADB, "reboot", "bootloader"])
    preflight("restore")
    check_image(STOCK, STOCK_HASH)
    write_recovery(STOCK, "restore")
    note("Factory recovery write acknowledged")
    run([*FASTBOOT, "reboot"], log="restore-reboot.log")
    if not wait_for(lambda: coloros("stock-after-restore.txt"), 150):
        raise RuntimeError("Stock write acknowledged, ColorOS boot pending")
    note("Verified stock ColorOS after restoration")


def main(check_super=False, a05_ram=False):
    if not SERIAL:
        raise SystemExit("Set RENO_ADB_SERIAL to the intended CPH2205 serial")
    image = RUN / "recovery.img"
    check_image(image, IMAGE_HASH)
    release = diagnostic_release(image)
    check_image(STOCK, STOCK_HASH)
    if not coloros("stock-before-test.txt"):
        raise RuntimeError("Reno not on expected ColorOS build")
    battery = run([*ADB, "shell", "dumpsys battery"])
    level = re.search(r"(?m)^\s*level:\s*(\d+)", battery)
    if not level or int(level[1]) < 20:
        raise RuntimeError("Battery below preflight level or unavailable")
    with (RUN / "test-start.txt").open("x", encoding="utf-8") as stamp:
        print(time.strftime("%Y-%m-%d %H:%M:%S %z"), file=stamp)
    run([*ADB, "reboot", "bootloader"])
    preflight("install")
    with (RUN / "usb-monitor-stdout.txt").open("w", encoding="utf-8") as out:
        monitor = subprocess.Popen(
            [sys.executable, "-u", str(RUN.parent/"read_recovery_console.py"),
             str(RUN/"physical-usb.log"), "--seconds", "195",
             "--a05-ram" if a05_ram else "--follow-ufs",
             "--expected-release", release,
             *(["--check-super"] if check_super else [])],
            stdout=out, stderr=subprocess.STDOUT)
        try:
            write_recovery(image, "install")
            run([*FASTBOOT, "reboot", "recovery"], log="boot-fastboot.log")
            note("Diagnostic recovery boot requested")
            # Give the previous bootloader transport time to disconnect.
            time.sleep(4)
        finally:
            try:
                restore()
            finally:
                if monitor.poll() is None:
                    monitor.terminate()
                    monitor.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-directory", type=Path)
    parser.add_argument("--expected-sha256")
    probe = parser.add_mutually_exclusive_group()
    probe.add_argument("--check-super", action="store_true",
                        help="Read and fingerprint the first LP metadata blocks after UFS is detected")
    probe.add_argument("--a05-ram", action="store_true",
                       help="Run the Samsung binary check from the RAM-only recovery")
    options = parser.parse_args()
    if options.candidate_directory is not None or options.expected_sha256 is not None:
        if not options.candidate_directory or not re.fullmatch("[0-9a-f]{64}", options.expected_sha256 or ""):
            parser.error("An experiment directory and its expected SHA-256 are both required")
        directory = options.candidate_directory.resolve(strict=True)
        if directory.parent != RUN.parent:
            parser.error("Use a recovery experiment in the kernel port workspace")
        RUN = directory
        IMAGE_HASH = options.expected_sha256
    if (RUN / "test-start.txt").exists():
        raise SystemExit("This physical trial already started")
    main(options.check_super, options.a05_ram)
