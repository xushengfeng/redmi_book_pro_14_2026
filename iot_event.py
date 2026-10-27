#!/usr/bin/env python3
"""Parse Xiaomi IoT module events.

The official IoTService.exe receives a small event frame from the IoT driver:

    byte 0: 0x01          (event/type)
    byte 1: EventFunc
    byte 2: EventValue

EventFunc 0x2A is the power action:
    value 1 -> shutdown
    value 2 -> sleep
    value 3 -> wake

This module only parses frames into an Event.  It does not parse policy and
does not execute anything; see iot_agent.py for that.
"""
from __future__ import annotations

import glob
import os
import re
import struct
import subprocess
from dataclasses import dataclass
from typing import Iterable, Iterator

from iot_mailbox import EVENT, Mailbox, Snapshot

EVENT_FUNC_STATUS = 0x29
EVENT_FUNC_POWER = 0x2A
EVENT_FUNC_STATE = 0x2B

POWER_ACTIONS = {
    1: "shutdown",
    2: "sleep",
    3: "wake",
}


@dataclass(frozen=True)
class Event:
    kind: str
    func: int
    value: int
    raw: bytes

    @property
    def is_power(self) -> bool:
        return self.kind in ("shutdown", "sleep", "wake")

    def __str__(self) -> str:
        return f"{self.kind} (func=0x{self.func:02x}, value={self.value})"


def parse_frame(data: bytes) -> Event | None:
    if len(data) < 3 or data[0] != 0x01:
        return None
    func, value = data[1], data[2]
    if func == EVENT_FUNC_POWER:
        kind = POWER_ACTIONS.get(value, "unknown")
    elif func == EVENT_FUNC_STATUS:
        kind = "status"
    elif func == EVENT_FUNC_STATE:
        kind = "state"
    else:
        kind = "unknown"
    return Event(kind, func, value, bytes(data[:3]))


KNOWN_FUNCS = {EVENT_FUNC_STATUS, EVENT_FUNC_POWER, EVENT_FUNC_STATE}


def find_frame(blob: bytes) -> Event | None:
    """Find the first plausible event frame in a mailbox snapshot."""
    for i, b in enumerate(blob):
        if b != 0x01 or i + 3 > len(blob):
            continue
        if blob[i + 1] not in KNOWN_FUNCS:
            continue
        return parse_frame(blob[i:i + 3])
    return None


def parse_text(line: str) -> Event:
    line = line.strip()
    if not line or line.startswith("#"):
        raise ValueError("empty event line")
    if line.lower() in POWER_ACTIONS.values():
        kind = line.lower()
        value = next(k for k, v in POWER_ACTIONS.items() if v == kind)
        return Event(kind, EVENT_FUNC_POWER, value, bytes([1, EVENT_FUNC_POWER, value]))
    parts = line.replace(",", " ").split()
    if len(parts) != 3:
        raise ValueError(f"expected '01 2a 01' or an action name, got {line!r}")
    raw = bytes(int(p, 16) for p in parts)
    ev = parse_frame(raw)
    if ev is None:
        raise ValueError(f"not an event frame: {raw.hex()}")
    return ev


def parse_wmi_payload(payload: int) -> Event | None:
    """redmi-wmi logs the raw MIFS payload as a decimal integer.

    76289 = 0x00012A01 -> little-endian bytes 01 2A 01 -> shutdown.
    """
    raw = payload.to_bytes(4, "little")
    for i in range(len(raw) - 2):
        if raw[i] == 0x01 and raw[i + 1] in KNOWN_FUNCS:
            return parse_frame(raw[i:i + 3])
    return None


class JournalEventSource:
    """Tail journalctl for redmi-wmi's unknown-payload debug line.

    This is a userspace bridge while the in-tree redmi-wmi driver does not
    expose raw event payloads.  Enable the debug line first:

        echo 'module redmi_wmi +p' > /sys/kernel/debug/dynamic_debug/control
    """

    PATTERN = re.compile(r"Unknown WMI event with payload (\d+)")

    def __init__(self, since: str | None = None, follow: bool = True):
        self.cmd = ["journalctl", "-k", "-o", "cat", "--no-pager"]
        if follow:
            self.cmd.append("-f")
        if since:
            self.cmd += ["--since", since]

    def __iter__(self) -> Iterator[Event]:
        proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE,
                                text=True, bufsize=1)
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                m = self.PATTERN.search(line)
                if not m:
                    continue
                ev = parse_wmi_payload(int(m.group(1)))
                if ev is not None:
                    yield ev
        finally:
            proc.terminate()
            proc.wait(timeout=2)


KEY_MACRO1 = 0x290
KEY_MACRO2 = 0x291
KEY_MACRO3 = 0x292

INPUT_ACTIONS = {
    KEY_MACRO1: "shutdown",
    KEY_MACRO2: "sleep",
    KEY_MACRO3: "wake",
}

INPUT_EVENT = struct.Struct("llHHi")  # struct input_event on 64-bit Linux
EV_KEY = 1


def action_from_input(type_: int, code: int, value: int) -> str | None:
    """Map one struct input_event to an action name (press only)."""
    if type_ != EV_KEY or value != 1:
        return None
    return INPUT_ACTIONS.get(code)


def find_input_device(name: str = "Redmibook WMI keys") -> str | None:
    for sysdev in glob.glob("/sys/class/input/event*"):
        try:
            with open(os.path.join(sysdev, "device/name")) as f:
                if f.read().strip() == name:
                    return "/dev/input/" + os.path.basename(sysdev)
        except OSError:
            continue
    return None


class InputEventSource:
    """Read the raw key events emitted by the patched redmi-wmi driver.

    The driver maps the IoT payloads 01 2A 01/02/03 to KEY_MACRO1/2/3.
    """

    def __init__(self, device: str | None = None,
                 name: str = "Redmibook WMI keys"):
        self.device = device or find_input_device(name)
        if not self.device:
            raise RuntimeError(f"input device not found: {name}")

    def __iter__(self) -> Iterator[Event]:
        with open(self.device, "rb") as f:
            while True:
                data = f.read(INPUT_EVENT.size)
                if not data:
                    return
                _, _, type_, code, value = INPUT_EVENT.unpack(data)
                kind = action_from_input(type_, code, value)
                if kind:
                    n = next(k for k, v in POWER_ACTIONS.items() if v == kind)
                    yield Event(kind, EVENT_FUNC_POWER, n,
                                bytes([1, EVENT_FUNC_POWER, n]))


class FileEventSource:
    """Read one event per line; useful for tests and manual replay."""

    def __init__(self, path: str):
        self.path = path

    def __iter__(self) -> Iterator[Event]:
        with open(self.path) as f:
            for line in f:
                if line.strip() and not line.lstrip().startswith("#"):
                    yield parse_text(line)


class MailboxEventSource:
    """Poll the 0xFE0B0E00 region and yield new event frames.

    This is deliberately conservative: it only yields when the region
    changes and a frame with a known EventFunc appears.  Use --dump while
    testing to see the raw bytes.
    """

    def __init__(self, mailbox: Mailbox | None = None,
                 dump: bool = False):
        self._own = mailbox is None
        self.mbox = mailbox or Mailbox()
        self.dump = dump
        self._last = self.mbox.snapshot()

    def close(self) -> None:
        if self._own:
            self.mbox.close()

    def __enter__(self) -> "MailboxEventSource":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def poll(self) -> Iterable[Event]:
        snap = self.mbox.snapshot()
        if snap.event == self._last.event and snap.status == self._last.status:
            return []
        old = self._last
        self._last = snap
        if self.dump:
            print("event:", snap.event.hex())
            print("status:", snap.status.hex())
        ev = find_frame(snap.event) or find_frame(snap.status)
        if ev is None:
            return []
        if ev.raw == b"\x00\x00\x00":
            return []
        return [ev]


def simulate(kind: str) -> Event:
    if kind not in POWER_ACTIONS.values():
        raise ValueError(f"unknown simulation {kind!r}")
    value = next(k for k, v in POWER_ACTIONS.items() if v == kind)
    return Event(kind, EVENT_FUNC_POWER, value, bytes([1, EVENT_FUNC_POWER, value]))
