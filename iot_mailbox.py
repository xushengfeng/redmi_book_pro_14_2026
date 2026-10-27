#!/usr/bin/env python3
"""Low-level access to the Xiaomi IoT module mailbox (EC/PMC SRAM).

The official Windows driver maps three physical ranges:

    0xFE0B0AB8   8 B      doorbell / control
    0xFE0B0E00   0x100 B  command / event buffer
    0xFE0B0F00   0x80 B   status / ack mailbox

This module only maps /dev/mem and reads/writes bytes.  It contains no
policy and no knowledge of status values or shutdown.
"""
from __future__ import annotations

import mmap
import os
from dataclasses import dataclass

BASE = 0xFE0B0000
PAGE = 0x1000
SPAN = 0x2000

DOORBELL = 0xFE0B0AB8
EVENT = 0xFE0B0E00
STATUS = 0xFE0B0F00

EVENT_SIZE = 0x100
STATUS_SIZE = 0x80
DOORBELL_SIZE = 8


@dataclass(frozen=True)
class Snapshot:
    doorbell: bytes
    event: bytes
    status: bytes

    def as_dict(self) -> dict[str, bytes]:
        return {
            "doorbell": self.doorbell,
            "event": self.event,
            "status": self.status,
        }


class Mailbox:
    def __init__(self, path: str = "/dev/mem"):
        self._f = open(path, "rb+", buffering=0)
        self._m = mmap.mmap(self._f.fileno(), SPAN, offset=BASE,
                            access=mmap.ACCESS_WRITE)

    def read(self, addr: int, length: int) -> bytes:
        if not BASE <= addr < BASE + SPAN:
            raise ValueError(f"address outside mailbox window: 0x{addr:x}")
        off = addr - BASE
        return bytes(self._m[off:off + length])

    def write(self, addr: int, data: bytes) -> None:
        if not BASE <= addr < BASE + SPAN:
            raise ValueError(f"address outside mailbox window: 0x{addr:x}")
        off = addr - BASE
        self._m[off:off + len(data)] = data

    def snapshot(self) -> Snapshot:
        return Snapshot(
            doorbell=self.read(DOORBELL, DOORBELL_SIZE),
            event=self.read(EVENT, EVENT_SIZE),
            status=self.read(STATUS, STATUS_SIZE),
        )

    def close(self) -> None:
        self._m.close()
        self._f.close()

    def __enter__(self) -> "Mailbox":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def changed_regions(a: Snapshot, b: Snapshot) -> dict[str, tuple[bytes, bytes]]:
    out = {}
    for name, old in a.as_dict().items():
        new = b.as_dict()[name]
        if old != new:
            out[name] = (old, new)
    return out
