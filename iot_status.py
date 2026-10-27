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

NAMES = {
    WIN_READY: "IOT_WIN_READY",
    SHUTING: "IOT_SHUTING",
    SUSPENDING: "IOT_SUSPENDING",
    FW_VERSION: "IOT_FW_VERSION",
    RESET_DEVICE: "IOT_RESET_DEVICE",
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


def get_firmware_version(bus: Mailbox | None = None) -> str:
    """Ask the module for its firmware version (e.g. 1.0.3_0010)."""
    own = bus is None
    mbox = bus or Mailbox()
    try:
        send_status(FW_VERSION, bus=mbox)
        time.sleep(0.1)
        resp = mbox.read(STATUS + 8, 0x100)
    finally:
        if own:
            mbox.close()
    length = resp[0]
    if length == 0 or length > 0x78:
        raise RuntimeError(f"bad firmware reply length {length}: {resp[:32].hex()}")
    return resp[1:1 + length].decode("ascii", "replace")


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
    args = ap.parse_args()

    if args.firmware:
        print(get_firmware_version())
        return

    if args.reset:
        resp = reset_module()
        print(f"reset sent; reply={resp.hex()}")
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
