#!/bin/busybox sh
set -u
export PATH=/bin

/bin/busybox mount -t proc proc /proc
/bin/busybox mount -t sysfs sysfs /sys
/bin/busybox --install -s /bin

usb_console() {
    mount -t configfs configfs /config || return 1
    udc=""
    for second in 1 2 3 4 5 6 7 8; do
        for controller in /sys/class/udc/*; do
            if [ -e "$controller" ]; then
                udc=${controller##*/}
                break
            fi
        done
        [ -n "$udc" ] && break
        sleep 1
    done
    [ -n "$udc" ] || return 1
    (
        mkdir /config/usb_gadget/cph2205_diag &&
        cd /config/usb_gadget/cph2205_diag &&
        printf '0x1d6b\n' >idVendor &&
        printf '0x0104\n' >idProduct &&
        printf '0x0200\n' >bcdUSB &&
        mkdir strings/0x409 configs/c.1 functions/acm.early &&
        printf 'CPH2205RAMDIAG\n' >strings/0x409/serialnumber &&
        printf 'CPH2205 RAM Diagnostics\n' >strings/0x409/product &&
        ln -s functions/acm.early configs/c.1/acm.early &&
        printf '%s\n' "$udc" >UDC &&
        IFS=: read -r major minor </sys/class/tty/ttyGS0/dev &&
        mknod -m 600 /dev/ttyGS0 c "$major" "$minor"
    )
}

want_usb=0
recovery_probe=0
serial_console=""
if [ -f /recovery-diagnostic ]; then
    want_usb=1
    recovery_probe=1
fi
for arg in $(/bin/busybox cat /proc/cmdline); do
    case "$arg" in
        cph2205.console=ttyAMA0)
            serial_console=/dev/ttyAMA0
            ;;
        cph2205.console=ttyS0)
            serial_console=/dev/ttyS0
            ;;
        cph2205.usb=1)
            want_usb=1
            ;;
        cph2205.recovery=1)
            recovery_probe=1
            ;;
    esac
done

if [ "$recovery_probe" -eq 1 ]; then
    (sleep 180; reboot -f) &
fi

kernel_log() {
    printf '%s\n' "$1" | while IFS= read -r line; do
        printf '<5>CPH2205 %s\n' "$line" 2>/dev/null >/dev/kmsg
    done
}

ufs_module=/lib/modules/ufs-mediatek.ko
if [ -r "$ufs_module" ]; then
    ufs_report="UFS module scheduled in 20 seconds"
    if [ -f /ufs-on-request ]; then
        ufs_report="UFS waiting for the USB shell request"
    fi
    kernel_log "$ufs_report"
    (
        if [ -f /ufs-on-request ]; then
            elapsed=0
            while [ ! -f /dev/cph2205-ufs-request ]; do
                sleep 1
                elapsed=$((elapsed + 1))
                case "$elapsed" in
                    2|5|15|30|60) kernel_log "initramfs awake after ${elapsed}s before UFS" ;;
                esac
            done
            kernel_log "received UFS request from USB shell"
        else
            sleep 20
        fi
        kernel_log "loading UFS module"
        if result=$(insmod "$ufs_module" 2>&1); then
            kernel_log "UFS module loaded"
            kernel_log "$(/bin/report-ufs)"
        else
            kernel_log "UFS module failed to load: $result"
        fi
    ) &
else
    ufs_report=$(/bin/report-ufs)
    kernel_log "$ufs_report"
fi

console_loop() {
    echo "CPH2205 initramfs online"
    uname -r
    printf '%s\n' "$ufs_report"
    while :; do
        if [ "$#" -gt 0 ]; then
            # Open this tty after setsid so the shell acquires it as its controlling terminal.
            setsid /bin/sh -c 'exec /bin/sh -i <"$1" >"$1" 2>&1' sh "$1"
        else
            setsid cttyhack /bin/sh -i
        fi
        sleep 1
    done
}

can_open_console() {
    if [ "$1" = /dev/ttyS0 ]; then
        port=$(grep '^0: uart:' /proc/tty/driver/serial 2>/dev/null)
        case "$port" in
            ''|*' uart:unknown '*) return 1 ;;
        esac
    fi
    timeout 2 /bin/sh -c ': <"$1" >"$1"' sh "$1" 2>/dev/null
}

if [ "$want_usb" -eq 1 ] && usb_console && can_open_console /dev/ttyGS0; then
    console_loop /dev/ttyGS0 </dev/ttyGS0 >/dev/ttyGS0 2>&1
fi
if [ -n "$serial_console" ] && can_open_console "$serial_console"; then
    console_loop "$serial_console" <"$serial_console" >"$serial_console" 2>&1
fi
console_loop
