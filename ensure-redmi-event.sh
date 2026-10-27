#!/bin/sh
# Make sure the patched redmi-wmi driver owns the WMI event device.
# Run as root (systemd ExecStartPre).
set -eu

ID=46C93E13-EE9B-4262-8488-563BCA757FEF-0
DEV=/sys/bus/wmi/devices/$ID

modprobe redmi_wmi || true

driver=$(basename "$(readlink -f "$DEV/driver" 2>/dev/null || true)")
if [ "$driver" != "redmi-wmi" ]; then
    echo "$ID" > /sys/bus/wmi/drivers/bitland-mifs-wmi/unbind 2>/dev/null || true
    echo "$ID" > /sys/bus/wmi/drivers/redmi-wmi/bind 2>/dev/null || true
fi
