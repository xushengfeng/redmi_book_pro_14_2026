#!/usr/bin/env python3
"""
MIFS IoT channel via acpi_call (read-only by default).

The in-tree bitland-mifs-wmi driver does not expose the raw MIFS command
interface.  This tool talks to the AML method \\_SB.PC00.WMID.WMAA directly
through /proc/acpi/call.  Load acpi_call first:

    sudo modprobe acpi_call

Then:

    sudo python3 mifs_iot.py status
    sudo python3 mifs_iot.py on --yes
    sudo python3 mifs_iot.py off --yes

Only function group 0x0C is used.  The module is the Xiaomi always-on IoT
module; powering it on/off changes EC RMTS.  Nothing here provisions Wi-Fi
or talks to the Xiaomi cloud.
"""

from __future__ import annotations

import argparse
import os
import sys

METHOD = r"\_SB.PC00.WMID.WMAA"
PROC = "/proc/acpi/call"
GET = 0xFA
SET = 0xFB

NAMES = {
    0x02: "present/LOTS",
    0x03: "power/RMTS",
    0x06: "mailbox",
}

# byte offsets in the 32-byte WMAA reply
STATUS = 1
FRD0 = 4
FRD1 = 6


def call(func: int, sub: int, value: int = 0, op: int = GET) -> list[int]:
    if not os.path.exists(PROC):
        sys.exit("acpi_call is not loaded; run: sudo modprobe acpi_call")
    buf = bytes([
        0x00, op, 0x00, func,
        sub & 0xFF, 0x00, value & 0xFF, 0x00,
    ]) + bytes(24)
    with open(PROC, "w") as f:
        f.write(f"{METHOD} 0 1 b{buf.hex()}")
    with open(PROC) as f:
        text = f.read().strip()
    text = text.replace("\x00", "").strip()
    if not text.startswith("{"):
        sys.exit(f"unexpected acpi_call reply: {text!r}")
    body = text.strip("{}").strip()
    return [int(x, 16) for x in body.split(",")]


def decode(reply: list[int]) -> tuple[int, int]:
    status = (reply[STATUS] << 8) | reply[STATUS - 1]
    value = int.from_bytes(bytes(reply[FRD1:FRD1 + 4]), "little")
    return status, value


def status() -> None:
    for sub in (0x02, 0x03, 0x06):
        reply = call(0x0C, sub)
        st, val = decode(reply)
        ok = "ok" if st == 0x8000 else f"unsupported (0x{st:04x})"
        print(f"0x0C/{sub:02x} {NAMES.get(sub, ''):12s} = {val}  [{ok}]")


def power(on: bool, assume_yes: bool) -> None:
    if not assume_yes:
        sys.exit("refusing to write without --yes")
    reply = call(0x0C, 0x03, 1 if on else 0, SET)
    st, _ = decode(reply)
    if st != 0x8000:
        sys.exit(f"SET rejected (status 0x{st:04x})")
    st2, val = decode(call(0x0C, 0x03))
    print(f"power -> {val} ({'on' if val else 'off'})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status", help="read LOTS/RMTS/mailbox")
    p.set_defaults(func=lambda a: status())
    p = sub.add_parser("on", help="power the IoT module on")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=lambda a: power(True, a.yes))
    p = sub.add_parser("off", help="power the IoT module off")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=lambda a: power(False, a.yes))
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
