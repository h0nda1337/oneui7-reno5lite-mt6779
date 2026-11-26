# One UI 7 del Galaxy A05 en el OPPO Reno5 Lite

Port experimental del Galaxy A05 SM-A055M al OPPO Reno5 Lite CPH2205 (MT6779). El kernel de pruebas ya ejecuta `/init` en el Reno y Windows reconoce su puerto serie USB. Quedan por verificar una sesión interactiva reproducible, el acceso a las particiones UFS y el arranque de One UI hasta el escritorio.

## Qué se ha comprobado

| Entorno | Resultado observado |
| --- | --- |
| Samsung A055MUBUGDZB2 | Se extrajeron la configuración y la versión del kernel de fábrica. La compilación experimental usa Android Common Kernel 6.6.89, la configuración extraída y parches para el MT6779. Sigue pendiente obtener y cotejar el código exacto de la compilación OEM. |
| Reno CPH2205, ColorOS F.72 | Linux presenta mensajes en la pantalla física. En una prueba se registró el host SCSI de UFS. Falta identificar discos y verificar las particiones `super` y `userdata`. |
| USB en el Reno | Windows detectó el puerto serie en las pruebas 6 y 7. En la sexta capturó el eco del comando. En la séptima recibió cero bytes y el Reno se reinició con la carga de UFS aún pendiente. Fastboot confirmó la escritura del recovery original y ColorOS F.72 completó el arranque. |
| USB en QEMU | La consola respondió antes y después de cargar el módulo UFS. Con la carga a petición, el módulo siguió ausente transcurridos 21 segundos y se cargó al recibir la orden de la consola. |
| One UI por DSU | Se empaquetaron `system`, `product` y `system_ext`. Con el kernel original del Reno se registró `bpfloader-failed`. La prueba de mapas BPF en QEMU cubre una parte del arranque de Android. |

El alcance actual de las pruebas físicas es el recovery temporal. El objetivo posterior es evaluar qué componentes MediaTek del A05 pueden adaptarse. La etapa del preloader requiere antes un arranque reproducible y una vía de recuperación comprobada. [Registro de pruebas y restauraciones](work/kernel-port/physical-trials.json)

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
    --boot-arg pd_ignore_unused --output local-recovery
python3 ../tools/avbtool.py verify_image --image local-recovery/recovery.img
```

Los manifiestos de `work/kernel-port/recovery-*/` registran la composición de cada imagen. El monitor físico usa PySerial. El ejecutor de las pruebas usa ADB y Fastboot con el número de serie que se indique en `RENO_ADB_SERIAL`.

## Reconstruir el experimento DSU

Desde `work/`, `inspect_super.py` extrae las particiones lógicas del donante a `a05/`. `mount_inputs.sh` monta las imágenes de ambos modelos en modo de lectura mediante EROFS. A continuación, `filter_ext_mapping.py` genera el ajuste de la política SELinux y `check_split_policy.sh` compara las combinaciones con el compilador `secilc`. `rebuild_system_ext.sh` reconstruye la partición con EROFS y `sign_system_ext.sh` genera la firma de desarrollo. Por último, `package_dsu.py` crea el ZIP y compara cada entrada con su imagen de origen. Una clave generada de nuevo cambia los hashes del paquete. [Sumas del experimento archivado](work/dsu/package-verification.txt)

Los archivos de Linux, OPPO y AOSP conservan sus avisos de origen. Los textos de licencia correspondientes están en [LICENSES](LICENSES).
