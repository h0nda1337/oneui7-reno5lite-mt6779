"""Adapt the Reno's MT6359 and diagnostic USB bindings for Linux 6.6."""

import argparse
from pathlib import Path

import libfdt


PMIC = "/soc/pwrap@1000d000/main_pmic"
LEGACY = f"{PMIC}/mt6359regulator"
NATIVE = f"{PMIC}/regulators"
SYMBOLS = "/__symbols__"
UFS = "/soc/ufshci@11270000"
USB = "/soc/usb0@11201000"
USB_PHY = "/soc/usb-phy0@11e40000"
USB_PHY_PORTS = ("usb2-phy0@11e40000", "usb3-phy0@11e40700")
SUPPLIES = {"vcc-supply": "ldo_vemc", "vccq2-supply": "ldo_vufs"}


def property_items(tree, path):
    position = tree.first_property_offset(tree.path_offset(path), (libfdt.NOTFOUND,))
    while position >= 0:
        prop = tree.get_property_by_offset(position)
        yield prop.name, prop.as_str()
        position = tree.next_property_offset(position, (libfdt.NOTFOUND,))


def validate_supplies(tree, container):
    controller = tree.path_offset(UFS)
    for prop, name in SUPPLIES.items():
        phandle = tree.getprop(controller, prop).as_uint32()
        supply_node = tree.path_offset(f"{container}/{name}")
        if tree.node_offset_by_phandle(phandle) != supply_node:
            raise ValueError(f"{prop} does not reference {name}")


def adapt_usb_phy_clocks(tree):
    provider = tree.path_offset(USB_PHY)
    if tree.getprop(provider, "clock-names").as_str() != "u3phya_ref":
        raise ValueError("Expected the stock shared USB PHY reference clock")
    reference_clock = bytes(tree.getprop(provider, "clocks"))
    if len(reference_clock) != 4:
        raise ValueError("Expected a single reference-clock phandle")
    clock = tree.node_offset_by_phandle(int.from_bytes(reference_clock, "big"))
    if tree.getprop(clock, "#clock-cells").as_uint32() != 0:
        raise ValueError("Expected a zero-cell reference clock")

    for port in USB_PHY_PORTS:
        node = tree.path_offset(f"{USB_PHY}/{port}")
        legacy = tree.getprop(node, "clocks", (libfdt.NOTFOUND,))
        if not isinstance(legacy, int):
            raise ValueError(f"The USB PHY port {port} already has clocks")
        tree.setprop(node, "clocks", reference_clock)
        tree.setprop_str(node, "clock-names", "ref")

    tree.delprop(tree.path_offset(USB_PHY), "clock-names")
    tree.delprop(tree.path_offset(USB_PHY), "clocks")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--diagnostic-usb", action="store_true")
    parser.add_argument("--stock-pmic", action="store_true",
                        help="Keep the legacy regulator layout for a stock-kernel control")
    args = parser.parse_args()

    data = args.input.read_bytes()
    tree = libfdt.Fdt(data)
    validate_supplies(tree, LEGACY)
    if tree.getprop(tree.path_offset(LEGACY), "compatible").as_str() != "mediatek,mt6359-regulator":
        raise ValueError("Unexpected legacy regulator binding")
    tree.resize(len(data) + 4096)
    symbol_changes = []
    if not args.stock_pmic:
        symbol_changes = [
            (name, NATIVE + value[len(LEGACY):])
            for name, value in property_items(tree, SYMBOLS)
            if value == LEGACY or value.startswith(f"{LEGACY}/")
        ]
        tree.set_name(tree.path_offset(LEGACY), "regulators")
        tree.delprop(tree.path_offset(NATIVE), "compatible")
        for name, value in symbol_changes:
            tree.setprop_str(tree.path_offset(SYMBOLS), name, value)

    if args.diagnostic_usb:
        controller = tree.path_offset(USB)
        if tree.getprop(controller, "compatible").as_str() != "mediatek,mtu3":
            raise ValueError("Unexpected USB controller")
        if tree.getprop(controller, "dr_mode").as_str() != "otg":
            raise ValueError("Expected the stock dual-role controller")
        tree.setprop_str(controller, "dr_mode", "peripheral")
        tree.delprop(tree.path_offset(USB), "usb-role-switch")
        if not args.stock_pmic:
            adapt_usb_phy_clocks(tree)

    tree.pack()
    validate_supplies(tree, LEGACY if args.stock_pmic else NATIVE)
    for _, path in symbol_changes:
        tree.path_offset(path)
    args.output.write_bytes(bytes(tree.as_bytearray()))
    binding = "stock" if args.stock_pmic else "Linux 6.6"
    print(f"Verified {binding} MT6359 binding and UFS supplies, updated {len(symbol_changes)} symbols")


if __name__ == "__main__":
    main()
