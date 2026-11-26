/* Check both reference clock sequences with the driver function under test. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint32_t u32;
typedef long ktime_t;
enum { REFCLK_REQ_TIMEOUT_US = 3000, REG_UFS_REFCLK_CTRL = 0x144 };
enum { REFCLK_RELEASE = 0, REFCLK_REQUEST = 1, REFCLK_ACK = 2 };
enum { PRE_CHANGE, POST_CHANGE };

struct arm_smccc_res { unsigned long a0; };
struct ufs_mtk_host {
    bool ref_clk_enabled;
    bool ref_clk_legacy_half_hw;
    unsigned int ref_clk_ungating_wait_us;
    unsigned int ref_clk_gating_wait_us;
};
struct ufs_hba { struct ufs_mtk_host *variant; void *dev; };

static char events[32];
static size_t event_count;
static u32 read_value;
static ktime_t clock_us;

static void note(char event)
{
    assert(event_count < sizeof(events) - 1);
    events[event_count++] = event;
    events[event_count] = '\0';
}

#define ufshcd_get_variant(hba) ((hba)->variant)
#define ktime_add_us(start, delay) ((start) + (delay))
#define ktime_before(now, deadline) ((now) < (deadline))
#define dev_err(dev, why, value) ((void)(dev), (void)(why), (void)(value), note('E'))
#define ufs_mtk_ref_clk_notify(on, stage, res) ((void)&(res), note_notify((on), (stage)))

static void note_notify(bool on, int stage)
{
    if (stage == PRE_CHANGE)
        note(on ? '+' : '-');
    else {
        assert(stage == POST_CHANGE);
        note(on ? 'O' : 'F');
    }
}

static void ufshcd_writel(struct ufs_hba *hba, u32 value, u32 reg)
{
    assert(hba && reg == REG_UFS_REFCLK_CTRL);
    assert(value == REFCLK_RELEASE || value == REFCLK_REQUEST);
    note(value == REFCLK_RELEASE ? 'R' : 'W');
}

static u32 ufshcd_readl(struct ufs_hba *hba, u32 reg)
{
    assert(hba && reg == REG_UFS_REFCLK_CTRL);
    note('?');
    return read_value;
}

static void ufshcd_delay_us(unsigned int delay, unsigned int slack)
{
    assert((delay == 20 || delay == 30) && slack == 10);
    note(delay == 20 ? 'U' : 'G');
}

static void usleep_range(unsigned int minimum, unsigned int maximum)
{
    assert(minimum == 100 && maximum == 200);
    note('S');
}

static ktime_t ktime_get(void)
{
    clock_us += 4000;
    return clock_us;
}

#include "ufs_refclk_under_test.inc"

static void check(struct ufs_hba *hba, bool on, int result, bool enabled, const char *expected)
{
    event_count = 0;
    events[0] = '\0';
    assert(ufs_mtk_setup_ref_clk(hba, on) == result);
    assert(hba->variant->ref_clk_enabled == enabled);
    if (strcmp(events, expected)) {
        fprintf(stderr, "expected [%s], got [%s]\n", expected, events);
        assert(false);
    }
}

int main(void)
{
    struct ufs_mtk_host host = {
        .ref_clk_legacy_half_hw = true,
        .ref_clk_ungating_wait_us = 20,
        .ref_clk_gating_wait_us = 30,
    };
    struct ufs_hba hba = { .variant = &host };

    check(&hba, true, 0, true, "+UR");
    check(&hba, true, 0, true, "");
    check(&hba, false, 0, false, "RG-");

    host.ref_clk_legacy_half_hw = false;
    read_value = REFCLK_REQUEST | REFCLK_ACK;
    check(&hba, true, 0, true, "+W?UO");
    read_value = REFCLK_RELEASE;
    check(&hba, false, 0, false, "-GR?F");
    read_value = REFCLK_REQUEST;
    check(&hba, true, -ETIMEDOUT, false, "+W?SEF");

    puts("UFS reference clock ordering verified for both modes and timeout recovery");
    return 0;
}
