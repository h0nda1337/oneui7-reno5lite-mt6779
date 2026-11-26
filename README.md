# One UI 7 del Galaxy A05 en el OPPO Reno5 Lite

Port experimental del Galaxy A05 SM-A055M al OPPO Reno5 Lite CPH2205 (MT6779). El kernel de pruebas ejecutó el `/init` de diagnóstico en el Reno, respondió a un comando desde Windows y detectó el almacenamiento interno UFS. El arranque de Android en el teléfono con este kernel y la llegada al escritorio de One UI siguen pendientes.

## Qué se ha comprobado

| Entorno | Resultado observado |
| --- | --- |
| Samsung A055MUBUGDZB2 | Se extrajeron la configuración y la versión del kernel de fábrica. La compilación experimental usa Android Common Kernel 6.6.89, la configuración extraída y parches para el MT6779. Sigue pendiente obtener y cotejar el código exacto de la compilación OEM. [Historial oficial del firmware](https://doc.samsungmobile.com/SM-A055M/028533231231/eng.html) |
| Reno CPH2205, ColorOS F.72 | En dos arranques independientes de la misma imagen, `ufshcd-mtk` reconoció tres unidades UFS y la consola identificó `super` en `sdc46` y `userdata` en `sdc49`. En la prueba 9 también se leyeron 12 KiB de metadatos de `super`, con la firma de cabecera LP esperada. |
| USB en el Reno | La prueba 7 terminó con cero bytes recibidos en Windows. Las pruebas 8 y 9 usaron los mismos componentes de arranque y añadieron solo `cpuidle.off=1` a la línea de comandos de la prueba 7. En cada arranque la consola devolvió el reto de esa sesión y la versión exacta del kernel. El monitor pidió entonces que se cargara el módulo UFS. |
| QEMU | En la prueba de referencia, la consola USB respondió antes y después de solicitar UFS. Al añadir `cpuidle.off=1`, el USB simulado respondió al primer reto y su controlador virtual falló después. Por UART se verificaron el latido a los 15 s y la carga de UFS a petición. |
| Binarios del A05 en QEMU Cortex-A55 | El enlazador de Samsung resolvió las 23 dependencias directas de `/system/bin/init`. El `toybox` de esa misma imagen ejecutó `uname -r` y devolvió la versión del kernel experimental. |
| Primera etapa de Android en QEMU | El `/init` original del A05 se ejecutó como PID 1, copió `build.prop` e inició la búsqueda de las particiones de primer montaje. En el control con la tabla de montaje de Samsung y sin discos, llegó al tiempo de espera de `super` y de otras 13 particiones. |
| Android y almacenamiento en QEMU | Con las ocho particiones lógicas originales del A05, sus dos `vbmeta` y las particiones `prism` y `optics` del CSC, Android montó diez sistemas de archivos en modo de lectura mediante dm-verity. Cargó la política SELinux, pasó a la segunda etapa de `init` y activó los tres paquetes APEX iniciales. El control de tres particiones se detiene al buscar `prism`. |
| One UI por DSU | Se empaquetaron `system`, `product` y `system_ext`. Con el kernel original del Reno se registró `bpfloader-failed`. La prueba de mapas BPF en QEMU cubre una parte del arranque de Android. |

Los dos arranques con consola y UFS terminaron en un reinicio automático. ColorOS registró `ocp` en ambos casos. La causa sigue abierta. Después de cada prueba Fastboot confirmó la escritura del recovery original y ColorOS F.72 completó el arranque. [Registro de pruebas y restauraciones](work/kernel-port/physical-trials.json)

El alcance actual de las pruebas físicas es el recovery temporal. El objetivo posterior es evaluar qué componentes MediaTek del A05 pueden adaptarse. La etapa del preloader requiere antes un arranque reproducible y una vía de recuperación comprobada.

## Entradas locales

Cada persona aporta sus imágenes de fábrica. Para el kernel se usan el archivo AP del SM-A055M y `boot.img`, `dtbo.img` y el respaldo de recovery del CPH2205 F.72. Para DSU también hacen falta las particiones de sistema del A05 y las particiones del Reno que usa el análisis de compatibilidad. Los manifiestos incluyen versiones y sumas SHA-256 para comprobar las entradas. Las claves de desarrollo se generan localmente.

## Compilar el kernel

Ejecuta estos pasos desde un clon del proyecto en Linux. La compilación usa Git, LLVM/Clang y LLD para arm64, las herramientas habituales de compilación del kernel, OpenSSL, `pahole` y Python 3. La extracción y las pruebas también usan `lz4`, `device-tree-compiler`, los bindings Python de libfdt, BusyBox estático para arm64 y QEMU para AArch64.

```bash
cd work/kernel-port
port_dir=$PWD
kernel=/var/tmp/codex-cph2205-linux-6.6.89
build=/var/tmp/codex-cph2205-build-6.6.89
git clone --depth 1 --branch android15-6.6-2025-06_r1 https://android.googlesource.com/kernel/common "$kernel"
test "$(git -C "$kernel" rev-parse HEAD)" = ebbf6177d5ae521e9902af2816ef56ff366bccfa

git -C "$kernel" apply "$port_dir"/{ubsan-location,mt6779-infra-clock-capacity,mt6779-eint-banks,mt6779-ufs-half-hw-refclk,mt6779-ufs-reset-log,mt6779-usb2-clock-status,mt6779-usb-peripheral-vbus,cph2205-lkfb-kbuild}.patch
install -m 644 cph2205-lkfb.c "$kernel/drivers/video/fbdev/"
bash configure_reno_kernel.sh Image display-ufs-module
```

La configuración del A05 incluida se puede volver a extraer con `python3 extract_a05_config.py "$A05_AP" a05`. El controlador USB de referencia de OPPO se coteja contra el commit `235dfa346382bc864a73f37d2753cd491a072570`. [Código publicado por OPPO](https://github.com/oppo-source/android_kernel_oppo_mt6779/tree/235dfa346382bc864a73f37d2753cd491a072570)

## Verificar antes de una prueba física

Desde `work/kernel-port`, define `RENO_FIRMWARE` como la carpeta de tus imágenes OPPO y `BUSYBOX_ARM64` como la ruta al ejecutable estático arm64. El siguiente flujo extrae el árbol de dispositivos, adapta los reguladores y el USB, y arranca el entorno en QEMU.

```bash
python3 extract_reno_tree.py "$RENO_FIRMWARE/boot.img" "$RENO_FIRMWARE/dtbo.img" reno-dt
python3 adapt_reno_pmic.py reno-dt/base-0.dtb reno-dt/reno-6.6-usb-ref-base.dtb --diagnostic-usb
bash build_early_ramdisk.sh recovery-ufs-on-request
make -C "$kernel" O="$build" ARCH=arm64 LLVM=1 CONFIG_USB_DUMMY_HCD=m -j8 drivers/usb/gadget/udc/dummy_hcd.ko drivers/usb/class/cdc-acm.ko
python3 check_usb_console_qemu.py --ramdisk ramdisk-recovery-ufs-on-request.cpio.gz --log qemu-usb-request.log
```

Para empaquetar en la PC, define `RENO_RECOVERY` como la ruta al respaldo original de F.72. El empaquetador comprueba el tamaño y el hash, reconstruye el contenido original y verifica cada componente del diagnóstico.

```bash
python3 prepare_recovery_kernel.py --kernel "$build/arch/arm64/boot/Image" \
    --dtb reno-dt/reno-6.6-usb-ref-base.dtb --ramdisk ramdisk-recovery-ufs-on-request.cpio.gz \
    --boot-arg cph2205.lkfb=1 --boot-arg console=tty0 --boot-arg clk_ignore_unused \
    --boot-arg pd_ignore_unused --boot-arg cpuidle.off=1 --output local-recovery
python3 ../tools/avbtool.py verify_image --image local-recovery/recovery.img
```

Los manifiestos de `work/kernel-port/recovery-*/` registran la composición de cada imagen. El monitor físico usa PySerial. El ejecutor de las pruebas usa ADB y Fastboot con el número de serie que se indique en `RENO_ADB_SERIAL`.

Con la imagen `system` del A05 montada como se indica en la sección de DSU, este comando comprueba el enlazador y ejecuta `toybox` en QEMU desde `work/kernel-port`. También requiere GNU `readelf`.

```bash
python3 check_android_userspace_qemu.py --system-root /tmp/codex-oneui-2205/a05/system \
    --kernel "$build/arch/arm64/boot/Image" --ramdisk ramdisk-recovery-ufs-on-request.cpio.gz \
    --log qemu-android-userspace.log
```

Para comprobar el primer proceso de Android, el siguiente control extrae `init_boot` y la tabla de montaje de `vendor_boot` del AP original. Arranca en QEMU con el kernel del port y comprueba en la consola que el PID 1 llega a esperar las particiones. Requiere GNU `cpio`.

```bash
python3 check_a05_first_stage_qemu.py --ap "$A05_AP" --kernel "$build/arch/arm64/boot/Image"
```

Para reproducir el control con tres particiones, define `A05_BOOT_IMAGES` como el directorio que contiene `init_boot.img`, `vbmeta.img` y `vbmeta_system.img` descomprimidos del AP. En el directorio `VIRTIO_MODULES` deben estar los módulos `virtio_pci_legacy_dev.ko`, `virtio_pci_modern_dev.ko`, `virtio_pci.ko` y `virtio_blk.ko` firmados para esta compilación. La herramienta usa `sfdisk`, presenta las imágenes en modo de lectura y guarda la salida en un archivo de registro nuevo.

```bash
python3 check_a05_virtio_mount_qemu.py --boot-images "$A05_BOOT_IMAGES" \
    --system ../a05/system.img --virtio-modules "$VIRTIO_MODULES" \
    --kernel "$build/arch/arm64/boot/Image" --log qemu-android-mount.log
```

Para llegar a la segunda etapa, usa el CSC de la misma compilación que el AP. Desde `work/kernel-port`, define `A05_CSC` como la ruta al archivo CSC `.tar.md5` y `CSC_RAW` como un directorio local nuevo. Estos pasos usan `simg2img` de Android y las imágenes de `super` extraídas en la sección de DSU.

```bash
mkdir "$CSC_RAW"
for p in prism optics; do
    tar -xOf "$A05_CSC" "$p.img.lz4" | lz4 -dc > "$CSC_RAW/$p.sparse"
    simg2img "$CSC_RAW/$p.sparse" "$CSC_RAW/$p.img"
done
python3 ../inspect_super.py ../a05-super.sparse.img --output ../a05 \
    --extract system_dlkm vendor_dlkm odm_dlkm
python3 check_a05_virtio_mount_qemu.py --boot-images "$A05_BOOT_IMAGES" \
    --system ../a05/system.img --virtio-modules "$VIRTIO_MODULES" \
    --csc-images "$CSC_RAW" --android-partitions ../a05 \
    --kernel "$build/arch/arm64/boot/Image" --log qemu-android-second-stage.log
```

El verificador calcula la medida AVB a partir de los metadatos originales de la cadena que presenta al invitado. Para A055MUBUGDZB2, la cadena de `vbmeta`, `prism` y `optics` mide 12 672 bytes y su SHA-256 es `274d234631f32e8f926bee199d3c9ce3e2d7f94e1f20032b507c831cfb114387`. Las ocho imágenes lógicas se verificaron además contra el `vbmeta_system` firmado. En el CSC de esta compilación, el primer bloque de cada imagen requiere la corrección de errores FEC de dm-verity. En QEMU las lecturas corregidas coincidieron con los hashes del árbol firmado.

## Reconstruir el experimento DSU

En Linux, usa Bash, Python 3.11 o posterior, GNU tar, `lz4`, OpenSSL, `secilc`, FUSE y las herramientas EROFS. El experimento usó `erofs-utils` 1.8.6 con `erofsfuse`, `mkfs.erofs` y `fsck.erofs` en el `PATH`. Define `A05_AP` como la ruta absoluta al archivo AP `.tar.md5` de A055MUBUGDZB2 y exporta `RENO_FIRMWARE` con la ruta absoluta a las cinco imágenes EROFS de ColorOS F.72 indicadas en el bloque.

```bash
# Desde la raíz del clon, en una misma sesión de Bash
set -euo pipefail
cd work
particiones=(system system_ext product vendor odm)
tar -xOf "$A05_AP" super.img.lz4 | lz4 -dc > a05-super.sparse.img
python3 inspect_super.py a05-super.sparse.img --output a05 --extract "${particiones[@]}"

# Si aportas el super completo del Reno en lugar de las cinco imágenes, define RENO_SUPER.
if [[ -n ${RENO_SUPER:-} ]]; then
    export RENO_FIRMWARE="$PWD/inputs/reno"
    python3 inspect_super.py "$RENO_SUPER" --output "$RENO_FIRMWARE" --extract "${particiones[@]}"
fi
bash mount_inputs.sh
bash check_split_policy.sh
test -s policy/a05-filtered-ext-map-with-reno-vendor.bin
bash rebuild_system_ext.sh
bash sign_system_ext.sh
```

La compilación de SELinux con el mapa original del A05 falla en esta combinación. En un clon nuevo, el archivo que comprueba `test -s` procede de la variante con el mapa 31.0 filtrado. Para comprobar que ese mapa es el único archivo modificado, monta la imagen reconstruida en modo de lectura, compárala con la original y empaqueta el ZIP.

```bash
montajes=/tmp/codex-oneui-2205
mkdir -p "$montajes/candidate/system_ext"
erofsfuse -o ro "$PWD/dsu/system_ext.img" "$montajes/candidate/system_ext"
python3 verify_system_ext.py
python3 package_dsu.py
umount "$montajes/candidate/system_ext"
for modelo in a05 reno; do
    for p in "${particiones[@]}"; do umount "$montajes/$modelo/$p"; done
done
```

El resultado es `outputs/OneUI7-A055M-CPH2205-DSU-exp01.zip` en la raíz del clon. El empaquetador verifica los SHA-256 de `system.img`, `product.img` y `system_ext.img` dentro del ZIP contra las imágenes locales. La clave de desarrollo y la sal AVB se generan localmente, de modo que la imagen firmada y el ZIP pueden tener hashes distintos de los archivados. [Sumas del experimento archivado](work/dsu/package-verification.txt)

Los archivos de Linux, OPPO y AOSP conservan sus avisos de origen. Los textos de licencia correspondientes están en [LICENSES](LICENSES).
