"""Resolve the clock, PHY, interrupt and regulator references for the selected Reno overlay."""

import argparse
import json
from pathlib import Path
import struct

import libfdt


UFS = "/soc/ufshci@11270000"
USB = "/soc/usb0@11201000"
PMIC_WRAP = "/soc/pwrap@1000d000"
PMIC = f"{PMIC_WRAP}/main_pmic"
UART = "/soc/serial@11002000"


def node_path(tree, node):
    parts = []
    while node > 0:
        parts.append(tree.get_name(node))
        node = tree.parent_offset(node)
    return "/" + "/".join(reversed(parts))


def read_cells(tree, node, key):
    value = tree.getprop(node, key)
    if len(value) % 4:
        raise ValueError(f"{node_path(tree, node)}: malformed {key}")
    return list(struct.unpack(f">{len(value) // 4}I", value))


def find_provider(tree, phandle):
    node = tree.node_offset_by_phandle(phandle)
    ancestor = node
    while ancestor > 0:
        status = tree.getprop(ancestor, "status", (libfdt.NOTFOUND,))
        if not isinstance(status, int) and status.as_str() not in ("ok", "okay"):
            raise ValueError(f"Disabled provider: {node_path(tree, ancestor)}")
        ancestor = tree.parent_offset(ancestor)
    return node


def provider_refs(tree, consumer, key, cells_property):
    cells = read_cells(tree, consumer, key)
    refs = []
    while cells:
        provider = find_provider(tree, cells[0])
        length = tree.getprop(provider, cells_property).as_uint32()
        if length >= len(cells):
            raise ValueError(f"{node_path(tree, consumer)}: truncated {key}")
        refs.append({"provider": node_path(tree, provider), "arguments": cells[1:1 + length]})
        cells = cells[1 + length:]
    return refs


def named(tree, node, property_name, names_property, cells_property):
    result = provider_refs(tree, node, property_name, cells_property)
    names = tree.getprop(node, names_property).as_stringlist()
    if len(names) != len(result) or len(set(names)) != len(names):
        raise ValueError(f"Invalid {names_property} for {node_path(tree, node)}")
    return dict(zip(names, result, strict=True))


def supplies(tree, node, names):
    result = {}
    for name in names:
        value = tree.getprop(node, f"{name}-supply").as_uint32()
        provider = find_provider(tree, value)
        result[name] = node_path(tree, provider)
    return result


def interrupts(tree, node):
    ancestor = node
    while ancestor >= 0:
        parent = tree.getprop(ancestor, "interrupt-parent", (libfdt.NOTFOUND,))
        if not isinstance(parent, int):
            break
        ancestor = tree.parent_offset(ancestor, (libfdt.BADOFFSET, libfdt.NOTFOUND))
    else:
        raise ValueError(f"No interrupt parent for {node_path(tree, node)}")
    provider = find_provider(tree, parent.as_uint32())
    arguments = read_cells(tree, node, "interrupts")
    count = tree.getprop(provider, "#interrupt-cells").as_uint32()
    if not count or len(arguments) % count:
        raise ValueError(f"Invalid interrupt layout for {node_path(tree, node)}")
    return {"provider": node_path(tree, provider),
            "specifiers": [arguments[i:i + count] for i in range(0, len(arguments), count)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tree", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    tree = libfdt.Fdt(args.tree.read_bytes())
    report = {}
    for path in (PMIC_WRAP, PMIC, UART, UFS, USB):
        node = tree.path_offset(path)
        report[path] = {
            "compatible": tree.getprop(node, "compatible").as_stringlist(),
            "interrupts": interrupts(tree, node),
        }
        if path != PMIC:
            report[path]["clocks"] = named(tree, node, "clocks", "clock-names", "#clock-cells")
        if path in (UFS, USB):
            report[path]["phys"] = provider_refs(tree, node, "phys", "#phy-cells")
            names = ("vcc", "vccq2") if path == UFS else ("vusb33",)
            report[path]["supplies"] = supplies(tree, node, names)
        if path == UFS:
            report[path]["resets"] = named(tree, node, "resets", "reset-names", "#reset-cells")
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Resolved the references for {len(report)} nodes")


if __name__ == "__main__":
    main()
