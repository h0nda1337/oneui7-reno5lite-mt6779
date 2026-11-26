"""Summarize firmware properties, policy files, and VINTF declarations."""

import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

ROOT = Path("/tmp/codex-oneui-2205")
PARTITIONS = ("system", "system_ext", "product", "vendor", "odm")
PROPERTY = re.compile(
    r"^ro\.(?:.+\.)?(?:build\.version\.(?:sdk|release|security_patch|incremental|oneui)"
    r"|first_api_level|vndk\.version|board\.platform|cpu\.abilist(?:32|64)?)$"
)


def inventory(device):
    result = {}
    for partition in PARTITIONS:
        mount = ROOT / device / partition
        if not mount.is_mount():
            raise ValueError(f"Missing filesystem at {mount}")
        root = mount / "system" if partition == "system" and (mount / "system").is_dir() else mount
        entries = result[partition] = {"root": str(root), "props": {}, "vintf": {},
                                       "sepolicy": {}, "init": [], "ui": []}
        for candidate in ("build.prop", "etc/build.prop", "default.prop", "etc/prop.default"):
            path = root / candidate
            if path.is_file():
                for line in path.read_text(errors="replace").splitlines():
                    key, _, value = line.partition("=")
                    if PROPERTY.fullmatch(key):
                        entries["props"][key] = value

        for path in sorted((root / "etc/vintf").rglob("*.xml")):
            xml = ET.parse(path).getroot()
            hal_entries = []
            for hal in xml.findall("hal"):
                interfaces = [
                    (interface.findtext("name"), [i.text for i in interface.findall("instance")])
                    for interface in hal.findall("interface")
                ]
                hal_entries.append({
                    "name": hal.findtext("name"),
                    "format": hal.get("format", "hidl"),
                    "optional": hal.get("optional"),
                    "versions": [node.text for node in hal.findall("version")],
                    "fqnames": [node.text for node in hal.findall("fqname")],
                    "interfaces": interfaces,
                    "override": hal.get("override"),
                })
            entries["vintf"][str(path.relative_to(root))] = {
                "kind": xml.tag,
                "attributes": xml.attrib,
                "hal": hal_entries,
                "kernel": [node.attrib for node in xml.findall("kernel")],
                "sepolicy": ET.tostring(node, encoding="unicode").strip()
                if (node := xml.find("sepolicy")) is not None else None,
            }

        for path in sorted((root / "etc/selinux").rglob("*")):
            if path.is_file():
                relative = str(path.relative_to(root))
                entries["sepolicy"][relative] = (
                    path.read_text(errors="replace").strip() if path.suffix == ".txt" else path.stat().st_size
                )

        entries["init"] = [str(path.relative_to(root)) for path in sorted((root / "etc/init").rglob("*.rc"))]
        for directory in ("app", "priv-app", "framework"):
            for path in sorted((root / directory).iterdir()) if (root / directory).is_dir() else []:
                if re.search(r"SystemUI|TouchWiz|Launcher|SecSettings|Samsung|Knox|ims", path.name, re.I):
                    entries["ui"].append(str(path.relative_to(root)))
    return result


if __name__ == "__main__":
    report = {device: inventory(device) for device in ("a05", "reno")}
    Path(__file__).with_name("firmware-inventory.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
