#!/usr/bin/env python3
"""Print raw input events from the Redmibook WMI keys device."""
import glob
import os
import struct
import sys

WANTED = "Redmibook WMI keys"
FMT = struct.Struct("llHHi")  # struct input_event on 64-bit Linux

KEY_NAMES = {
    148: "KEY_PROG1", 149: "KEY_PROG2", 202: "KEY_PROG3", 203: "KEY_PROG4",
    0x290: "KEY_MACRO1", 0x291: "KEY_MACRO2", 0x292: "KEY_MACRO3",
}


def find_device(name=WANTED):
    for sysdev in glob.glob("/sys/class/input/event*"):
        try:
            with open(os.path.join(sysdev, "device/name")) as f:
                if f.read().strip() == name:
                    return "/dev/input/" + os.path.basename(sysdev)
        except OSError:
            continue
    return None


def main():
    dev = sys.argv[1] if len(sys.argv) > 1 else find_device()
    if not dev:
        print(f"device not found: {WANTED}", flush=True)
        return 1
    print(f"reading {dev}", flush=True)
    with open(dev, "rb") as f:
        while True:
            data = f.read(FMT.size)
            if not data:
                break
            sec, usec, typ, code, val = FMT.unpack(data)
            if typ == 1:  # EV_KEY
                name = KEY_NAMES.get(code, str(code))
                print(f"EV_KEY {name} value={val}", flush=True)
            elif typ != 0:
                print(f"type={typ} code={code} value={val}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
