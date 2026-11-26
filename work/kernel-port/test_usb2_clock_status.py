"""Exercise the MT6779 USB2 clock checks against mocked IPPC status registers."""

from pathlib import Path
import subprocess
import tempfile


DRIVER = Path("/var/tmp/codex-cph2205-linux-6.6.89/drivers/usb/mtu3/mtu3_plat.c")
MOCK = r"""
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#include <stdint.h>

typedef uint32_t u32;
#define __iomem
enum { USB_DR_MODE_PERIPHERAL = 2, USB_SPEED_HIGH = 3, USB_SPEED_SUPER = 5 };
enum { U3D_SSUSB_IP_PW_STS1 = 0, U3D_SSUSB_IP_PW_STS2 = 4 };
enum { SSUSB_SYS125_RST_B_STS = 1, SSUSB_SYSPLL_STABLE = 2, SSUSB_REF_RST_B_STS = 4 };
enum { SSUSB_U2_MAC_SYS_RST_B_STS = 1 };

struct mtu3 { int max_speed; };
struct ssusb_mtk {
    void *ippc_base;
    const char *dev;
    int dr_mode;
    struct mtu3 *u3d;
};

static bool mt6779 = true;
static unsigned int polls;
static u32 regs[2];

static u32 read_status(const u32 *reg)
{
    polls |= 1u << (reg - regs);
    return *reg;
}

#define of_machine_is_compatible(compatible) ((void)(compatible), mt6779)
#define dev_info(dev, fmt, ...) ((void)(dev), (void)(fmt))
#define dev_err(dev, fmt, ...) ((void)(dev), (void)(fmt))
#define readl_poll_timeout(addr, value, cond, delay, timeout) \
    ((void)(delay), (void)(timeout), (value) = read_status(addr), (cond) ? 0 : -ETIMEDOUT)
"""

CHECKS = r"""
static void check(struct ssusb_mtk *usb, int expected_result, unsigned int expected_polls)
{
    polls = 0;
    assert(ssusb_check_clocks(usb, 0) == expected_result);
    assert(polls == expected_polls);
}

int main(void)
{
    struct mtu3 device = { .max_speed = USB_SPEED_HIGH };
    struct ssusb_mtk usb = { .ippc_base = regs, .dr_mode = USB_DR_MODE_PERIPHERAL,
                             .u3d = &device };

    regs[1] = SSUSB_U2_MAC_SYS_RST_B_STS;
    check(&usb, 0, 2);
    regs[1] = 0;
    check(&usb, -ETIMEDOUT, 2);

    device.max_speed = USB_SPEED_SUPER;
    check(&usb, -ETIMEDOUT, 1);
    device.max_speed = USB_SPEED_HIGH;
    mt6779 = false;
    check(&usb, -ETIMEDOUT, 1);

    mt6779 = true;
    usb.dr_mode = 1;
    usb.u3d = 0;
    regs[0] = SSUSB_SYS125_RST_B_STS | SSUSB_SYSPLL_STABLE | SSUSB_REF_RST_B_STS;
    regs[1] = SSUSB_U2_MAC_SYS_RST_B_STS;
    check(&usb, 0, 3);
    return 0;
}
"""


def main():
    source = DRIVER.read_text()
    start = source.index("int ssusb_check_clocks(")
    end = source.index("\nstatic int wait_for_ip_sleep(", start)
    with tempfile.TemporaryDirectory(prefix="mt6779-usb2-") as directory:
        fixture = Path(directory) / "test.c"
        executable = fixture.with_suffix("")
        fixture.write_text(MOCK + source[start:end] + CHECKS)
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", fixture, "-o", executable],
                       check=True)
        subprocess.run([executable], check=True)
    print("Verified MT6779 USB2 and standard clock checks, including timeouts")


if __name__ == "__main__":
    main()
