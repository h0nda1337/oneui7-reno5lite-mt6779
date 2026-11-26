#include <inttypes.h>
#include <linux/bpf.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/syscall.h>
#include <unistd.h>

/* Prefix of each 192-byte .android_maps record in the A05 Tethering test.o. */
struct android_map {
    uint32_t type, key_size, value_size, max_entries, flags;
    unsigned char metadata[172];
};

_Static_assert(sizeof(struct android_map) == 192, "Invalid Android map record size");

static void fail(const char *operation)
{
    perror(operation);
    exit(EXIT_FAILURE);
}

static int bpf(enum bpf_cmd command, union bpf_attr *args, const char *operation)
{
    int result = syscall(SYS_bpf, command, args, sizeof(*args));

    if (result < 0)
        fail(operation);
    return result;
}

static void probe_map(const struct android_map *map, unsigned int index)
{
    unsigned char key[32] = {0};
    unsigned char value[32] = {0x2a};
    unsigned char got[32] = {0};
    char path[64];
    int fd, pinned;
    union bpf_attr args = {
        .map_type = map->type,
        .key_size = map->key_size,
        .value_size = map->value_size,
        .max_entries = map->max_entries,
        .map_flags = map->flags,
    };

    if ((map->type != BPF_MAP_TYPE_HASH && map->type != BPF_MAP_TYPE_ARRAY) ||
        !map->key_size || map->key_size > sizeof(key) ||
        !map->value_size || map->value_size > sizeof(value) || map->max_entries < 2) {
        fprintf(stderr, "Unexpected map parameters in record %u\n", index);
        exit(EXIT_FAILURE);
    }
    fd = bpf(BPF_MAP_CREATE, &args, "BPF_MAP_CREATE");

    key[0] = 1;
    args = (union bpf_attr){.map_fd = fd, .key = (uintptr_t)key,
                           .value = (uintptr_t)value, .flags = BPF_ANY};
    bpf(BPF_MAP_UPDATE_ELEM, &args, "BPF_MAP_UPDATE_ELEM");

    snprintf(path, sizeof(path), "/sys/fs/bpf/a05_test_%u", index);
    args = (union bpf_attr){.bpf_fd = fd, .pathname = (uintptr_t)path};
    bpf(BPF_OBJ_PIN, &args, "BPF_OBJ_PIN");
    close(fd);
    args.bpf_fd = 0;
    pinned = bpf(BPF_OBJ_GET, &args, "BPF_OBJ_GET");
    args = (union bpf_attr){.map_fd = pinned, .key = (uintptr_t)key,
                           .value = (uintptr_t)got};
    bpf(BPF_MAP_LOOKUP_ELEM, &args, "BPF_MAP_LOOKUP_ELEM");
    if (memcmp(value, got, map->value_size)) {
        fprintf(stderr, "Unexpected value read back from record %u\n", index);
        exit(EXIT_FAILURE);
    }
    close(pinned);
    if (unlink(path))
        fail("unlink pinned map");
    printf("Map %u verified (type=%" PRIu32 ", key=%" PRIu32 ", value=%" PRIu32 ")\n",
           index, map->type, map->key_size, map->value_size);
}

int main(int argc, char **argv)
{
    struct android_map maps[4];
    FILE *records;

    if (argc != 2) {
        fprintf(stderr, "Usage: %s extracted-android-map-records\n", argv[0]);
        return EXIT_FAILURE;
    }
    records = fopen(argv[1], "rb");
    if (!records)
        fail("open map records");
    if (fread(maps, sizeof(maps), 1, records) != 1 || fgetc(records) != EOF ||
        ferror(records)) {
        fprintf(stderr, "Expected four complete A05 Tethering map records\n");
        return EXIT_FAILURE;
    }
    fclose(records);

    if (mount("bpffs", "/sys/fs/bpf", "bpf", 0, NULL))
        fail("mount bpffs");
    for (unsigned int index = 0; index < sizeof(maps) / sizeof(maps[0]); index++)
        probe_map(&maps[index], index);
    puts("A05 Tethering maps verified");
    return EXIT_SUCCESS;
}
