#!/bin/sh
# Build and install the redmi-wmi module with IoT raw-event keymap entries.
# Run with sudo.  Re-run after a kernel upgrade.
set -eu

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
KVER=$(uname -r)
KBASE=${KVER%%-*}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

URL="https://raw.githubusercontent.com/torvalds/linux/v${KBASE}/drivers/platform/x86/redmi-wmi.c"
if ! curl -fsSL -o "$WORK/redmi-wmi.c" "$URL"; then
    curl -fsSL -o "$WORK/redmi-wmi.c" \
        "https://raw.githubusercontent.com/torvalds/linux/master/drivers/platform/x86/redmi-wmi.c"
fi

patch -d "$WORK" -p1 < "$DIR/patches/redmi-wmi-iot-keys.patch"
printf 'obj-m := redmi-wmi.o\n' > "$WORK/Makefile"
make -C "/lib/modules/$KVER/build" M="$WORK" modules
install -Dm644 "$WORK/redmi-wmi.ko" "/lib/modules/$KVER/updates/redmi-wmi.ko"
depmod -a "$KVER"

echo "installed /lib/modules/$KVER/updates/redmi-wmi.ko"
echo "reload with: sudo rmmod redmi_wmi && sudo modprobe redmi_wmi"
echo "or reboot; iot-agent.service will bind the event device."
