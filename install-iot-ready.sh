#!/bin/sh
# Install the IoT status ack units.  Run with sudo from this directory.
set -eu

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

cat >/etc/systemd/system/iot-ready.service <<EOF
[Unit]
Description=Report IOT_WIN_READY to the Xiaomi IoT module

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $DIR/iot_status.py 0x10
TimeoutStartSec=15

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/iot-resume.service <<EOF
[Unit]
Description=Report IOT_WIN_READY to the Xiaomi IoT module after resume
After=suspend.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $DIR/iot_status.py 0x10
TimeoutStartSec=15

[Install]
WantedBy=suspend.target
EOF

systemctl daemon-reload
systemctl enable --now iot-ready.service
systemctl enable iot-resume.service

echo "installed: iot-ready.service, iot-resume.service"
echo "check: systemctl status iot-ready.service"
