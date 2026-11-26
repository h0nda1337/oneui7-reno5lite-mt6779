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


def shell_challenge():
    token = secrets.token_hex(3).encode()
    command = b"printf '\\nR%s %s\\n' " + token + b" \"$(uname -r)\" | tee /dev/kmsg\r"
    response = re.compile(rb"(?m)^R" + token + rb" 6\.6\.89-cph2205-lab[^\s]*\r*\n")
    return token, command, response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seconds", type=int, default=50)
    parser.add_argument("--follow-ufs", action="store_true",
                        help="Keep polling the shell after its first response for the UFS partitions")
    args = parser.parse_args()
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
                    token, challenge, response = shell_challenge()
                    print(f"Waiting for R{token.decode()} and the kernel release on {port.device}", flush=True)
                    verified_connection = False
                    while time.monotonic() < deadline:
                        if time.monotonic() >= next_command:
                            if not verified_connection:
                                stream.write(b"\x03\r")
                                time.sleep(0.2)
                            stream.write(COMMAND if verified_connection else challenge)
                            next_command = time.monotonic() + (4 if verified_connection else 1)
                        if data := stream.read(4096):
                            captured.extend(data)
                            logfile.write(data)
                            logfile.flush()
                        if not verified_connection and response.search(captured):
                            verified_connection = shell_observed = True
                            print("Confirmed the physical shell returned its kernel release", flush=True)
                            if args.follow_ufs:
                                stream.write(REQUEST_UFS)
                            next_command = time.monotonic() + 1
                        if verified_connection and PROOF.search(captured):
                            if not args.follow_ufs or all(pattern.search(captured) for pattern in UFS_DISKS):
                                print(captured.decode("utf-8", "replace"))
                                return
            except serial.SerialException as error:
                print(f"Diagnostic serial disconnected: {error}", flush=True)
    if shell_observed:
        print("The shell responded, but both UFS partitions were not observed")
        return
    raise SystemExit("No complete response from the diagnostic serial console")


if __name__ == "__main__":
    main()
