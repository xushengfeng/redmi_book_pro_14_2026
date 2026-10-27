#!/bin/sh
# Install and enable the IoT event agent (raw input source).
# Run with sudo from this directory.
set -eu

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

cat >/etc/systemd/system/iot-agent.service <<EOF
[Unit]
Description=Xiaomi IoT event agent (shutdown/sleep/wake)
After=multi-user.target

[Service]
Type=simple
ExecStartPre=/bin/sh $DIR/ensure-redmi-event.sh
ExecStart=/usr/bin/python3 $DIR/iot_agent.py --source input --execute --allow-shutdown --allow-sleep
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now iot-agent.service

echo "installed and started: iot-agent.service"
echo "check: systemctl status iot-agent.service"
echo "stop:  sudo systemctl disable --now iot-agent.service"
