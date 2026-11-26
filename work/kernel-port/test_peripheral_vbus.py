"""Compare MT6779 peripheral VBUS register writes with the OPPO driver."""

from pathlib import Path
import subprocess
import tempfile


HERE = Path(__file__).resolve().parent
KERNEL = Path("/var/tmp/codex-cph2205-linux-6.6.89")
VENDOR = "235dfa346382bc864a73f37d2753cd491a072570"
MOCK = """
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
typedef uint32_t u32;
#define SSUSB_U2_CTRL(n) (n)
#define U3D_MISC_CTRL 0
#define SSUSB_U2_PORT_OTG_SEL (1U << 7)
#define VBUS_FRC_EN 1U
#define VBUS_ON 2U
static unsigned int writes;
static u32 mtu3_readl(u32 *reg, unsigned int offset) { return reg[offset]; }
static void mtu3_writel(u32 *reg, unsigned int offset, u32 value) { reg[offset] = value; writes++; }
static void mtu3_setbits(u32 *r, unsigned int n, u32 mask) { mtu3_writel(r, n, r[n] | mask); }
static void mtu3_clrbits(u32 *r, unsigned int n, u32 mask) { mtu3_writel(r, n, r[n] & ~mask); }
struct mtu3 { u32 *ippc_base, *mac_base; bool force_vbus; };
struct ssusb_mtk { u32 *ippc_base, *mac_base; bool force_vbus; };
"""
TEST = """
int main(void)
{
    for (int opt = 0; opt < 2; opt++) {
        for (unsigned int initial = 0; initial < 8; initial++) {
            u32 ippc = (0xabcdef00U & ~SSUSB_U2_PORT_OTG_SEL) | ((initial & 4) << 5);
            u32 mac = 0xdeadbe00U | (initial & 3);
            u32 vendor_ippc = ippc, vendor_mac = mac;
            struct mtu3 device = { &ippc, &mac, opt };
            struct ssusb_mtk vendor = { &vendor_ippc, &vendor_mac, opt };
            const bool sequence[] = { false, true, true, false, false, true };
            for (unsigned int i = 0; i < sizeof(sequence) / sizeof(sequence[0]); i++) {
                writes = 0;
                ssusb_set_force_vbus(&vendor, sequence[i]);
                const unsigned int vendor_writes = writes;
                writes = 0;
                mtu3_set_peripheral_vbus(&device, sequence[i]);
                assert(ippc == vendor_ippc && mac == vendor_mac);
                assert(writes == vendor_writes && writes == (opt ? 2U : 0U));
            }
        }
    }
    return 0;
}
"""


def function(source, declaration):
    start = source.index(declaration)
    return source[start:source.index("\n}", start) + 2]


def main():
    vendor = subprocess.check_output(["git", "-C", HERE/"oppo-mt6779", "show",
                                      f"{VENDOR}:drivers/usb/mtu3/mtu3_plat.c"], text=True)
    core = (KERNEL/"drivers/usb/mtu3/mtu3_core.c").read_text()
    source = "\n".join([MOCK, function(vendor, "void ssusb_set_force_vbus("),
                        function(core, "static void mtu3_set_peripheral_vbus("), TEST])
    with tempfile.TemporaryDirectory() as temp:
        binary = Path(temp)/"check-vbus"
        subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-x", "c", "-", "-o", binary],
                       input=source, text=True, check=True)
        subprocess.run([binary], check=True)
    print("MT6779 VBUS register values and write counts match the OPPO driver across transitions")


if __name__ == "__main__":
    main()
