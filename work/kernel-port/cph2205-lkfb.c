// SPDX-License-Identifier: GPL-2.0-only
/* Diagnostic access to the CPH2205 display surface initialized by LK. */
#include <asm/unaligned.h>
#include <linux/init.h>
#include <linux/ioport.h>
#include <linux/mm.h>
#include <linux/of.h>
#include <linux/of_reserved_mem.h>
#include <linux/platform_data/simplefb.h>
#include <linux/platform_device.h>
#include <linux/string.h>

static bool cph2205_lkfb_enabled __initdata;

static int __init cph2205_lkfb_setup(char *arg)
{
	cph2205_lkfb_enabled = !strcmp(arg, "1");
	return 1;
}
__setup("cph2205.lkfb=", cph2205_lkfb_setup);

static int __init cph2205_lkfb_init(void)
{
	static const char panel[] = "oppo20291_samsung_ams643xy01_1080p_dsi_vdo";
	const struct simplefb_platform_data mode = {
		.width = 1080,
		.height = 2400,
		.stride = 4352,
		.format = "a8r8g8b8",
	};
	const size_t frame_size = mode.height * mode.stride;
	struct device_node *node;
	struct reserved_mem *area;
	struct platform_device *device;
	struct resource surface;
	const u8 *tag;
	u64 address;
	u32 length;
	int tag_size;

	if (!cph2205_lkfb_enabled || !of_machine_is_compatible("mediatek,MT6779"))
		return 0;

	/* LK supplies this packed little-endian tag. The split properties in
	 * the factory DT can point at an unrelated surface.
	 */
	node = of_find_node_by_path("/chosen");
	tag = of_get_property(node, "atag,videolfb", &tag_size);
	if (!tag || tag_size < 20 + sizeof(panel) ||
	    !get_unaligned_le32(tag + 8) || memcmp(tag + 20, panel, sizeof(panel))) {
		of_node_put(node);
		return -ENODEV;
	}
	address = get_unaligned_le64(tag);
	length = get_unaligned_le32(tag + 16);
	of_node_put(node);

	node = of_find_node_by_path("/reserved-memory/mblock-16-framebuffer");
	if (!of_device_is_compatible(node, "mediatek,framebuffer") ||
	    !of_property_read_bool(node, "no-map") || of_property_read_bool(node, "reusable")) {
		of_node_put(node);
		return -ENODEV;
	}
	area = of_reserved_mem_lookup(node);
	of_node_put(node);
	if (!area || !address || !PAGE_ALIGNED(address) || address != area->base ||
	    frame_size > length || length > area->size || address > U64_MAX - frame_size)
		return -EINVAL;

	surface = (struct resource)DEFINE_RES_MEM(address, frame_size);
	device = platform_device_register_resndata(NULL, "simple-framebuffer", PLATFORM_DEVID_AUTO,
						   &surface, 1, &mode, sizeof(mode));
	return PTR_ERR_OR_ZERO(device);
}
arch_initcall(cph2205_lkfb_init);
