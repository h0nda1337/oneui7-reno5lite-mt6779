"""Compare compatibility declarations and footprint without modifying the source images."""

from pathlib import Path
import json
import os
import re
import struct
import zlib

WORK = Path(__file__).parent
RENO_BOOT = Path(os.environ.get("RENO_FIRMWARE", WORK / "inputs/reno")) / "boot.img"


def boot_kernel(path):
    with path.open("rb") as file:
        header = file.read(4096)
        if header[:8] != b"ANDROID!":
            raise ValueError("Unrecognized Android boot image")
        size = struct.unpack_from("<I", header, 8)[0]
        version = struct.unpack_from("<I", header, 40)[0]
        offset = 4096 if version >= 3 else struct.unpack_from("<I", header, 36)[0]
        if version > 4 or not 2048 <= offset <= 65536:
            raise ValueError("Invalid boot header")
        file.seek(offset)
        kernel = file.read(size)
        if len(kernel) != size:
            raise ValueError("Truncated kernel")
    magic = kernel[:4]
    if magic.startswith(b"\x1f\x8b"):
        kernel = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(kernel, 128 * 1024 * 1024)
    signature = re.search(rb"Linux version [ -~]{8,240}", kernel)
    return {"boot_header": version, "kernel_magic": magic.hex(),
            "kernel": signature.group().decode() if signature else None}


def declarations(partitions, sides, kind):
    output = {}
    for side in sides:
        for filename, source in partitions[side]["vintf"].items():
            if source["attributes"].get("type") != kind:
                continue
            for hal in source["hal"]:
                output.setdefault(hal["name"], []).append({"partition": side, "file": filename, **hal})
    return output


def print_declarations(label, matrix, provided):
    absent = sorted(set(matrix) - set(provided))
    print(label)
    for name in absent:
        for item in matrix[name]:
            print(f"  {name} {item['format']} {item['versions']} {item['partition']}/{item['file']}")


def main():
    inventory = json.loads((WORK / "firmware-inventory.json").read_text())
    samsung, reno = inventory["a05"], inventory["reno"]
    samsung_framework = ("system", "system_ext", "product")
    hardware = ("vendor", "odm")
    print("Reno kernel:", boot_kernel(RENO_BOOT))
    for label, device in inventory.items():
        policy = device["vendor"]["sepolicy"].get("etc/selinux/plat_sepolicy_vers.txt")
        system = Path(device["system"]["root"])
        vndk = [entry.name for entry in sorted((system / "apex").glob("*vndk*"))]
        print(label, "vendor policy:", policy, "VNDK APEX:", vndk)

    framework = declarations(samsung, samsung_framework, "framework")
    actual_reno = {name: [item for item in entries if not item["file"].split("/")[-1].startswith(
        ("manifest_ss", "manifest_qsqs", "manifest_tsts"))]
                   for name, entries in declarations(reno, hardware, "device").items()}
    reno_manifest = {name: [item for item in entries if "compatibility_matrix" not in item["file"]]
                     for name, entries in actual_reno.items()}
    reno_manifest = {name: entries for name, entries in reno_manifest.items() if entries}
    required = {name: [item for item in entries if item["file"] in {
        "etc/vintf/compatibility_matrix.5.xml", "etc/vintf/compatibility_matrix.device.xml"
    } and item["optional"] != "true"] for name, entries in framework.items()}
    print_declarations(
        "Samsung FCM 5 and device-specific required HAL names missing in Reno manifests:",
        {name: entries for name, entries in required.items() if entries}, reno_manifest,
    )

    reno_matrix = reno["vendor"]["vintf"]["etc/vintf/compatibility_matrix.xml"]["hal"]
    print("Reno vendor-to-framework compatibility declarations:", reno_matrix)
    system_matrix = samsung["system"]["vintf"]["etc/vintf/compatibility_matrix.5.xml"]
    print("Samsung FCM 5 kernel declarations:", system_matrix["kernel"])
    print("Samsung FCM 5 policy declarations:", system_matrix["sepolicy"])

    for partition in ("system", "system_ext", "product"):
        path = Path(samsung[partition]["root"])
        print(partition, "top-level:", " ".join(entry.name for entry in sorted(path.iterdir())))
        print(partition, "UI:", " ".join(item for item in samsung[partition]["ui"] if
                                        re.search("SystemUI|TouchWiz|SecSettings|Launcher", item, re.I)))


if __name__ == "__main__":
    main()
