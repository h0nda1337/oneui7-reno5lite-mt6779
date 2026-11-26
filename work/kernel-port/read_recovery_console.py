"""Read the CPH2205 diagnostic USB console without opening storage devices."""

import argparse
from pathlib import Path
import re
import secrets
import time

import serial
from serial.tools import list_ports


IDENTITY = "CPH2205RAMDIAG"
REQUEST_UFS = b"test ! -f /ufs-on-request || : > /dev/cph2205-ufs-request\r"
COMMAND = (
    b"printf '\\nCPH2205 PROBE START\\n'; uname -r; "
    b"test ! -f /recovery-diagnostic || echo recovery-diagnostic; "
    b"ls /sys/class/block; "
    b"for p in /sys/bus/platform/devices/11270000.*/driver; do "
    b"test ! -L $p || readlink $p; done; "
    b"if test -x /bin/report-ufs; then /bin/report-ufs /sys 0; fi; "
    b"dmesg | grep -E 'CPH2205 (UFS|loading)|11270000.ufshci' | tail -n 24; "
    b"printf 'CPH2205 PROBE END\\n'\r"
)
PROOF = re.compile(rb"(?m)^CPH2205 PROBE END\r*\n")
UFS_DISKS = [re.compile(rb"(?m)^UFS partition " + name + rb" on [^\s]+\r*\n")
             for name in (b"super", b"userdata")]
SUPER_NODE = re.compile(rb"(?m)^UFS partition super on (sd[a-z]+[1-9][0-9]*)\r*\n")
LP_SAMPLE = re.compile(rb"(?m)^CPH2205 LP ([0-9a-f]{64}) ([0-9a-f]{8})\r*\n")


def super_metadata_command(node):
    # The temporary node and 12 KiB sample live on the initramfs. Only super is opened.
    if not re.fullmatch(rb"sd[a-z]+[1-9][0-9]*", node):
        raise ValueError("Unexpected UFS partition name")
    return (
        b"(set -e; p=/sys/class/block/" + node + b"; "
        b"grep -Fxq PARTNAME=super \"$p/uevent\"; "
        b"case \"$(readlink -f \"$p\")\" in */11270000.ufshci/host*/*) ;; *) exit 1;; esac; "
        b"IFS=: read -r maj min <\"$p/dev\"; d=$(mktemp -d /dev/lp.XXXXXX); "
        b"trap 'rm -f \"$d/b\" \"$d/p\"; rmdir \"$d\"' EXIT; "
        b"mknod -m 400 \"$d/b\" b \"$maj\" \"$min\"; "
        b"dd if=\"$d/b\" of=\"$d/p\" bs=4096 skip=1 count=3 2>/dev/null; "
        b"test \"$(wc -c <\"$d/p\")\" -eq 12288; "
        b"sum=$(sha256sum \"$d/p\"); "
        b"magic=$(od -An -tx1 -j8192 -N4 \"$d/p\" | tr -d ' \\n'); "
        b"printf '\\nCPH2205 LP %s %s\\n' \"${sum%% *}\" \"$magic\")\r"
    )


def shell_challenge(expected_release=None):
    token = secrets.token_hex(3).encode()
    command = b"printf '\\nR%s %s\\n' " + token + b" \"$(uname -r)\" | tee /dev/kmsg\r"
    release = re.escape(expected_release.encode("ascii")) if expected_release else rb"6\.6\.89-cph2205-lab[^\s]*"
    response = re.compile(rb"(?m)^R" + token + b" " + release + rb"\r*\n")
    return token, command, response


def check_a05_ram(stream, captured, logfile, expected_release, deadline):
    token = secrets.token_hex(8).encode()
    command = (
        b"(set -e; test -f /a05-ram-probe && test -f /dev/a05-userspace.log; "
        b"export ANDROID_ROOT=/system LD_LIBRARY_PATH=/system/lib64/bootstrap:/system/lib64; "
        b"cat /dev/a05-userspace.log; grep -Fxq A05_CHECK_EXIT=0 /dev/a05-userspace.log; "
        b"cat /dev/a05-pmic.log; "
        b"sha256sum /system/bin/bootstrap/linker64 /system/bin/init /system/bin/toybox; "
        b"! grep -q '^ufs_mediatek ' /proc/modules || exit 1; "
        b"test ! -e /sys/class/block/sdc; "
        b"r=$(/system/bin/bootstrap/linker64 /system/bin/toybox uname -r); "
        b"/system/bin/bootstrap/linker64 /system/bin/toybox echo A05_NATIVE "
        + token + b" \"$r\")\r"
    )
    proof = re.compile(rb"(?m)^A05_NATIVE " + token + b" " +
                       re.escape(expected_release.encode("ascii")) + rb"\r*\n")
    next_command = 0
    print(f"Waiting for Samsung toybox response {token.decode()}", flush=True)
    while time.monotonic() < deadline:
        if time.monotonic() >= next_command:
            stream.write(command)
            next_command = time.monotonic() + 2
        data = stream.read(4096)
        if not data:
            continue
        captured.extend(data)
        logfile.write(data)
        logfile.flush()
        if proof.search(captured):
            print("Verified Samsung toybox challenge on the physical RAM diagnostic", flush=True)
            stream.write(b": > /dev/a05-reboot-request && printf '\\nA05_RETURN %s\\n' " + token + b"\r")
            print("Requested the controlled return to ColorOS", flush=True)
            return True
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seconds", type=int, default=50)
    parser.add_argument("--expected-release", help="Require the exact release embedded in the recovery kernel")
    parser.add_argument("--follow-ufs", action="store_true",
                        help="Keep polling the shell after its first response for the UFS partitions")
    parser.add_argument("--check-super", action="store_true",
                        help="Read the LP geometry and first header block of the identified super partition")
    parser.add_argument("--a05-ram", action="store_true",
                        help="Verify Samsung binaries from RAM while UFS stays inactive")
    args = parser.parse_args()
    if args.a05_ram and (args.follow_ufs or args.check_super or not args.expected_release):
        parser.error("--a05-ram requires an exact kernel release and a RAM-only session")
    if args.check_super:
        args.follow_ufs = True
    deadline = time.monotonic() + args.seconds
    shell_observed = False
    with args.output.open("ab") as logfile:
        while time.monotonic() < deadline:
            choices = [port for port in list_ports.comports() if
                       (port.vid, port.pid, port.serial_number) == (0x1d6b, 0x0104, IDENTITY)]
            if len(choices) > 1:
                raise RuntimeError("More than one matching diagnostic port")
            if not choices:
                time.sleep(1)
                continue
            port = choices[0]
            print(f"Diagnostic USB serial enumerated on {port.device}", flush=True)
            try:
                with serial.Serial(port.device, 115200, timeout=0.5, write_timeout=1) as stream:
                    captured = bytearray()
                    next_command = 0.0
                    token, challenge, response = shell_challenge(args.expected_release)
                    print(f"Waiting for R{token.decode()} and the kernel release on {port.device}", flush=True)
                    verified_connection = False
                    metadata_command = None
                    while time.monotonic() < deadline:
                        if time.monotonic() >= next_command:
                            if not verified_connection:
                                stream.write(b"\x03\r")
                                time.sleep(0.2)
                            stream.write((metadata_command or COMMAND) if verified_connection else challenge)
                            next_command = time.monotonic() + (4 if verified_connection else 1)
                        if data := stream.read(4096):
                            captured.extend(data)
                            logfile.write(data)
                            logfile.flush()
                        if not verified_connection and response.search(captured):
                            verified_connection = shell_observed = True
                            print("Confirmed the physical shell returned its kernel release", flush=True)
                            if args.a05_ram:
                                if check_a05_ram(stream, captured, logfile, args.expected_release, deadline):
                                    return
                                break
                            if args.follow_ufs:
                                stream.write(REQUEST_UFS)
                            next_command = time.monotonic() + 1
                        if verified_connection and PROOF.search(captured):
                            if args.follow_ufs and not all(p.search(captured) for p in UFS_DISKS):
                                continue
                            if args.check_super and not LP_SAMPLE.search(captured):
                                match = SUPER_NODE.search(captured)
                                if match and metadata_command is None:
                                    metadata_command = super_metadata_command(match[1])
                                    next_command = 0
                                continue
                            print(captured.decode("utf-8", "replace"))
                            return
            except serial.SerialException as error:
                print(f"Diagnostic serial disconnected: {error}", flush=True)
    if shell_observed:
        if args.a05_ram:
            raise SystemExit("The Samsung userspace challenge did not complete")
        print("The shell responded, but a requested storage check remained incomplete")
        return
    raise SystemExit("No complete response from the diagnostic serial console")


if __name__ == "__main__":
    main()
