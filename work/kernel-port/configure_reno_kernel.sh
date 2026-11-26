#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
workspace=$PWD

source_dir=/var/tmp/codex-cph2205-linux-6.6.89
output_dir=/var/tmp/codex-cph2205-build-6.6.89
expected_commit=ebbf6177d5ae521e9902af2816ef56ff366bccfa
[[ $(git -C "$source_dir" rev-parse HEAD) == "$expected_commit" ]]
mkdir -p "$output_dir"
fragments=("$workspace/reno-bringup.config")
resolved_config=reno-bringup.resolved.config
variant=${2:-}
if [[ $variant == display || $variant == display-ufs-module ]]; then
    fragments+=("$workspace/reno-display.config")
    resolved_config=reno-display.resolved.config
fi
if [[ $variant == display-ufs-module ]]; then
    fragments+=("$workspace/reno-ufs-module.config")
    resolved_config=reno-ufs-module.resolved.config
fi

export ARCH=arm64 LLVM=1
if [[ -x "$workspace/host/usr/bin/pahole" ]]; then
    export PAHOLE=${PAHOLE:-"$workspace/host/usr/bin/pahole"}
else
    export PAHOLE=${PAHOLE:-pahole}
fi
"$PAHOLE" --version
(
    cd "$source_dir"
    scripts/kconfig/merge_config.sh -m -O "$output_dir" \
        "$workspace/a05/a05-stock.config" "${fragments[@]}"
    make O="$output_dir" PAHOLE="$PAHOLE" olddefconfig
)

while IFS= read -r setting; do
    grep -Fqx -- "$setting" "$output_dir/.config" || {
        echo "Required setting unresolved: $setting" >&2
        exit 1
    }
done < <(awk -F= '/^CONFIG_/ { required[$1] = $0 } END { for (name in required) print required[name] }' "${fragments[@]}")
cp -- "$output_dir/.config" "$workspace/$resolved_config"
printf 'MediaTek bring-up config resolved at %s\n' "$expected_commit"

make -C "$source_dir" O="$output_dir" PAHOLE="$PAHOLE" -j8 \
    drivers/tty/serial/8250/8250_mtk.o \
    drivers/pinctrl/mediatek/pinctrl-mt6779.o \
    drivers/clk/mediatek/clk-mt6779.o \
    drivers/soc/mediatek/mtk-pmic-wrap.o \
    drivers/mfd/mt6397-core.o \
    drivers/regulator/mt6359-regulator.o \
    drivers/reset/reset-ti-syscon.o \
    drivers/phy/mediatek/phy-mtk-ufs.o \
    drivers/ufs/host/ufs-mediatek.o

if [[ ${1:-} == Image ]]; then
    make -C "$source_dir" O="$output_dir" PAHOLE="$PAHOLE" -j8 Image
    if [[ $variant == display-ufs-module ]]; then
        make -C "$source_dir" O="$output_dir" PAHOLE="$PAHOLE" -j8 drivers/ufs/host/ufs-mediatek.ko
        mkdir -p "$workspace/ufs-module"
        module="$workspace/ufs-module/ufs-mediatek.ko"
        cp -- "$output_dir/drivers/ufs/host/ufs-mediatek.ko" "$module"
        "$output_dir/scripts/sign-file" sha1 "$output_dir/certs/signing_key.pem" \
            "$output_dir/certs/signing_key.x509" "$module"
    fi
fi
