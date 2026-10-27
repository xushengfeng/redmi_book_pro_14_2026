#!/usr/bin/env python3
"""MIFS control for performance mode, charge limit and microphone mute.

Uses the ACPI WMAA method through acpi_call.  Run as root:

    sudo python3 mifs.py perf get
    sudo python3 mifs.py perf set 2
    sudo python3 mifs.py charge get
    sudo python3 mifs.py charge set 80
    sudo python3 mifs.py mic get
    sudo python3 mifs.py mic mute
    sudo python3 mifs.py mic unmute

The protocol was reversed from the DSDT/SSDT WMAA method and cross-checked
with the bitland-mifs-wmi v2 mapping.
"""
from __future__ import annotations

import argparse
import os
import sys

METHOD = r"\_SB.PC00.WMID.WMAA"
PROC = "/proc/acpi/call"
GET = 0xFA
SET = 0xFB
OK = 0x8000

PERF_MODES = {
    2: "quiet",
    3: "balanced",
    4: "speed",
    9: "extreme",
    10: "extreme2",
}

CHARGE_CODES = {
    0: 100,
    1: 80,
    4: 80,
    5: 70,
    6: 60,
    7: 50,
    8: 40,
}
CHARGE_LEVELS = {
    100: 0,
    80: 4,
    70: 5,
    60: 6,
    50: 7,
    40: 8,
}


class MifsError(RuntimeError):
    pass


def call(func: int, sub: int, value: int = 0, op: int = GET) -> bytes:
    if not os.path.exists(PROC):
        raise MifsError("acpi_call is not loaded; run: sudo modprobe acpi_call")
    buf = bytes([
        0x00, op, 0x00, func,
        sub & 0xFF, 0x00, value & 0xFF, 0x00,
    ]) + bytes(24)
    with open(PROC, "w") as f:
        f.write(f"{METHOD} 0 1 b{buf.hex()}")
    with open(PROC) as f:
        text = f.read().replace("\x00", "").strip()
    if not text.startswith("{"):
        raise MifsError(f"unexpected acpi_call reply: {text!r}")
    data = bytes(int(x, 16) for x in text.strip("{}").strip().split(","))
    if len(data) < 10:
        raise MifsError(f"short reply: {data.hex()}")
    status = (data[1] << 8) | data[0]
    if status != OK:
        raise MifsError(f"firmware rejected the command (status 0x{status:04x})")
    return data


def value32(reply: bytes) -> int:
    return int.from_bytes(reply[6:10], "little")


def perf_get() -> int:
    return call(0x08, 0x00)[4]


def perf_set(mode: int) -> int:
    call(0x08, mode, 0, SET)
    return perf_get()


def charge_get() -> tuple[int, int]:
    reply = call(0x10, 0x02)
    code = value32(reply)
    return code, CHARGE_CODES.get(code, -1)


def charge_set(percent: int) -> tuple[int, int]:
    if percent not in CHARGE_LEVELS:
        raise MifsError("supported levels: 40, 50, 60, 70, 80, 100")
    call(0x10, 0x02, CHARGE_LEVELS[percent], SET)
    return charge_get()


def mic_get() -> tuple[int, bool]:
    """Return (raw, muted).  Raw 0 -> MIUT=1 -> muted."""
    raw = value32(call(0x0A, 0x05))
    return raw, raw == 0


def mic_set(mute: bool) -> tuple[int, bool]:
    # FUN4=0 -> MIUT=1 (mute), FUN4=1 -> MIUT=0 (unmute)
    call(0x0A, 0x05, 0 if mute else 1, SET)
    return mic_get()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("perf", help="performance mode")
    ps = p.add_subparsers(dest="action", required=True)
    ps.add_parser("get")
    pset = ps.add_parser("set")
    pset.add_argument("mode", type=int, help="2=quiet 3=balanced 4=speed 9/10=extreme")

    p = sub.add_parser("charge", help="charge limit")
    cs = p.add_subparsers(dest="action", required=True)
    cs.add_parser("get")
    cset = cs.add_parser("set")
    cset.add_argument("percent", type=int, choices=sorted(CHARGE_LEVELS))

    p = sub.add_parser("mic", help="microphone mute")
    ms = p.add_subparsers(dest="action", required=True)
    ms.add_parser("get")
    ms.add_parser("mute")
    ms.add_parser("unmute")

    args = ap.parse_args()
    try:
        if args.cmd == "perf":
            if args.action == "get":
                mode = perf_get()
                print(f"perf {mode} ({PERF_MODES.get(mode, 'unknown')})")
            else:
                mode = perf_set(args.mode)
                print(f"perf {mode} ({PERF_MODES.get(mode, 'unknown')})")
        elif args.cmd == "charge":
            if args.action == "get":
                code, pct = charge_get()
                print(f"charge code={code} limit={pct}%")
            else:
                code, pct = charge_set(args.percent)
                print(f"charge code={code} limit={pct}%")
        elif args.cmd == "mic":
            if args.action == "get":
                raw, muted = mic_get()
                print(f"mic raw={raw} {'muted' if muted else 'unmuted'}")
            else:
                raw, muted = mic_set(args.action == "mute")
                print(f"mic raw={raw} {'muted' if muted else 'unmuted'}")
    except MifsError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
