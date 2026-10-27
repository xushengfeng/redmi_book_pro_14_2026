#!/usr/bin/env python3
"""Send a host-status acknowledgement to the Xiaomi IoT module.

Reverse-engineered from IoTService.exe 25.0.0.9:

    ReportLaptopStatus(status):
        0xFE0B0F01 <- status, 01, 01, <4-byte detail>
        0xFE0B0F00 <- 0x55
        0xFE0B0F00 <- 0x55, status, 01, 02
        0xFE0B0F00 <- 0x55, status, 01, 02, 0x55, status, 01, 03

IOT_WIN_READY = 0x10, IOT_SHUTING = 0x0e, IOT_SUSPENDING = 0x0f.
Only the mailbox is touched; no EC registers.
"""
from __future__ import annotations

import argparse
import time

from iot_mailbox import Mailbox, STATUS

WIN_READY = 0x10
SHUTING = 0x0E
SUSPENDING = 0x0F
FW_VERSION = 0x0A
RESET_DEVICE = 0x03
WIFI_STATUS = 0x07
MODEL = 0x0B
CONNECT_WIFI = 0x0C

NAMES = {
    WIN_READY: "IOT_WIN_READY",
    SHUTING: "IOT_SHUTING",
    SUSPENDING: "IOT_SUSPENDING",
    FW_VERSION: "IOT_FW_VERSION",
    RESET_DEVICE: "IOT_RESET_DEVICE",
    WIFI_STATUS: "IOT_WIFI_STATUS",
    MODEL: "IOT_MODEL",
    CONNECT_WIFI: "IOT_CONNECT_WIFI",
}

WIFI_STATUS_NAMES = {
    1: "CannotConfigOver30Min",
    2: "WaitingConfigInBroadcast",
    3: "NotConnected",
    4: "ConnectedToNetwork",
    5: "ConnectedToCloud",
    6: "OtaInProgress",
    7: "WiFiClosed",
}


def send_status(status: int, detail: bytes = b"\x00\x00\x00\x00",
                bus: Mailbox | None = None) -> list[bytes]:
    """Send one status sequence; returns the mailbox snapshots between steps."""
    if not 0 <= status <= 0xFF:
        raise ValueError("status must be a byte")
    if len(detail) != 4:
        raise ValueError("detail must be exactly 4 bytes")

    own = bus is None
    mbox = bus or Mailbox()
    shots: list[bytes] = []
    try:
        mbox.write(STATUS + 1, bytes([status, 0x01, 0x01]) + detail)
        time.sleep(0.05)
        shots.append(mbox.read(STATUS, 16))

        mbox.write(STATUS, b"\x55")
        time.sleep(0.05)
        shots.append(mbox.read(STATUS, 16))

        mbox.write(STATUS, bytes([0x55, status, 0x01, 0x02]))
        time.sleep(0.10)
        shots.append(mbox.read(STATUS, 16))

        mbox.write(STATUS, bytes([0x55, status, 0x01, 0x02,
                                  0x55, status, 0x01, 0x03]))
        time.sleep(0.10)
        shots.append(mbox.read(STATUS, 16))
    finally:
        if own:
            mbox.close()
    return shots


def _read_reply_string(mbox: Mailbox) -> str:
    resp = mbox.read(STATUS + 8, 0x100)
    length = resp[0]
    if length == 0 or length > 0x78:
        raise RuntimeError(f"bad reply length {length}: {resp[:32].hex()}")
    return resp[1:1 + length].decode("ascii", "replace")


def get_firmware_version(bus: Mailbox | None = None) -> str:
    """Ask the module for its firmware version (e.g. 1.0.3_0010)."""
    own = bus is None
    mbox = bus or Mailbox()
    try:
        send_status(FW_VERSION, bus=mbox)
        time.sleep(0.1)
        return _read_reply_string(mbox)
    finally:
        if own:
            mbox.close()


def get_model(bus: Mailbox | None = None) -> str:
    """Read the module model (status 0x0B, e.g. xiaomi.laptop.n57sr)."""
    own = bus is None
    mbox = bus or Mailbox()
    try:
        send_status(MODEL, bus=mbox)
        time.sleep(0.1)
        return _read_reply_string(mbox)
    finally:
        if own:
            mbox.close()


def parse_wifi_reply(resp: bytes) -> str:
    """Parse the status 0x07 reply; SSID length is at offset 4."""
    if len(resp) < 5:
        return ""
    length = resp[4]
    if not 0 < length <= 0x77 or 5 + length > len(resp):
        return ""
    return resp[5:5 + length].decode("utf-8", "replace")


def get_wifi_status(bus: Mailbox | None = None) -> tuple[bytes, bytes, str]:
    """Read raw Wi-Fi status header/reply and the SSID (status 0x07)."""
    own = bus is None
    mbox = bus or Mailbox()
    try:
        send_status(WIFI_STATUS, bus=mbox)
        time.sleep(0.2)
        header = mbox.read(STATUS, 8)
        resp = mbox.read(STATUS + 8, 0x100)
        return header, resp[:8], parse_wifi_reply(resp)
    finally:
        if own:
            mbox.close()


def connect_wifi(bus: Mailbox | None = None) -> tuple[bytes, bytes]:
    """Ask the module to reconnect to its saved Wi-Fi (status 0x0C)."""
    own = bus is None
    mbox = bus or Mailbox()
    try:
        send_status(CONNECT_WIFI, bus=mbox)
        time.sleep(0.2)
        return mbox.read(STATUS, 16), mbox.read(STATUS + 8, 16)
    finally:
        if own:
            mbox.close()


def reset_module(bus: Mailbox | None = None) -> bytes:
    """Soft-reset the IoT module via the official status 0x03 sequence.

    This is the mailbox path behind IoTService IPC type 0x6A (ResetDevice).
    It does not cut EC power; the module reboots its own firmware/Wi-Fi.
    """
    own = bus is None
    mbox = bus or Mailbox()
    try:
        send_status(RESET_DEVICE, bus=mbox)
        time.sleep(0.2)
        return mbox.read(STATUS, 16)
    finally:
        if own:
            mbox.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("status", nargs="?", type=lambda x: int(x, 0),
                    default=WIN_READY)
    ap.add_argument("--detail", default="00000000",
                    help="4-byte detail field as hex (default 00000000)")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--firmware", action="store_true",
                    help="read the IoT module firmware version")
    ap.add_argument("--reset", action="store_true",
                    help="soft-reset the IoT module (status 0x03)")
    ap.add_argument("--wifi-status", action="store_true",
                    help="read the module Wi-Fi status and SSID (status 0x07)")
    ap.add_argument("--model", action="store_true",
                    help="read the module model (status 0x0B)")
    ap.add_argument("--reconnect", action="store_true",
                    help="ask the module to reconnect Wi-Fi (status 0x0C)")
    args = ap.parse_args()

    if args.firmware:
        print(get_firmware_version())
        return

    if args.reset:
        resp = reset_module()
        print(f"reset sent; reply={resp.hex()}")
        return

    if args.wifi_status:
        header, reply, ssid = get_wifi_status()
        code = header[4]
        name = WIFI_STATUS_NAMES.get(code, "unknown")
        print(f"wifi status: {code} ({name}) ssid={ssid!r}")
        print(f"  header={header.hex()} reply={reply.hex()}")
        return

    if args.model:
        print(get_model())
        return

    if args.reconnect:
        header, reply = connect_wifi()
        print(f"connect wifi sent; header={header.hex()} reply={reply.hex()}")
        return

    detail = bytes.fromhex(args.detail)
    shots = send_status(args.status, detail)
    if not args.quiet:
        name = NAMES.get(args.status, f"0x{args.status:02x}")
        print(f"sent {name} ({args.status:#04x})")
        for i, shot in enumerate(shots, 1):
            print(f"  step {i}: {shot.hex()}")


if __name__ == "__main__":
    main()
