#!/usr/bin/env python3
"""
NTAG 5 (NTA5332) I2C tool for the Redmi Book Pro 14 2026.

The laptop's NFC is an NXP NTAG 5 boost tag (ACPI NTAG5332 on I2C-1,
address 0x54), not a card reader.  This tool talks to the tag over
i2c-dev and can dump/restore its EEPROM and read/write NDEF.

No kernel driver is required; the kernel only needs i2c-dev.
"""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import os
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

I2C_RDWR = 0x0707
I2C_SLAVE = 0x0703

BUS_DEFAULT = "/dev/i2c-1"
ADDR_DEFAULT = 0x54
USER_SIZE = 0x800  # 2048 bytes of user EEPROM
BLOCK = 4


class I2CMsg(ctypes.Structure):
    _fields_ = [
        ("addr", ctypes.c_uint16),
        ("flags", ctypes.c_uint16),
        ("len", ctypes.c_uint16),
        ("buf", ctypes.POINTER(ctypes.c_uint8)),
    ]


class I2CRdwrData(ctypes.Structure):
    _fields_ = [
        ("msgs", ctypes.POINTER(I2CMsg)),
        ("nmsgs", ctypes.c_uint32),
    ]


class Ntag5:
    def __init__(self, bus: str = BUS_DEFAULT, addr: int = ADDR_DEFAULT):
        self.bus = bus
        self.addr = addr
        self.fd = os.open(bus, os.O_RDWR)
        fcntl.ioctl(self.fd, I2C_SLAVE, addr)
        # The tag's I2C target answers the first read after an idle period
        # with 0xff while it wakes up.  Prime it with a throw-away read.
        try:
            self._xfer(b"\x00\x00", BLOCK)
        except OSError:
            pass

    # NOTE: the NTAG 5 I2C address is a *block* address, one block = 4 bytes.
    # So byte offset 0x7f0 is block 0x1fc.  Never pass byte offsets straight
    # to the tag.

    def read_blocks(self, block: int, length: int) -> bytes:
        return self._xfer(bytes([(block >> 8) & 0xFF, block & 0xFF]), length)

    def read_bytes(self, byte_offset: int, length: int) -> bytes:
        if byte_offset % BLOCK:
            raise ValueError("byte offset must be 4-byte aligned")
        return self.read_blocks(byte_offset // BLOCK, length)

    def wake(self) -> None:
        try:
            self._xfer(b"\x00\x00", BLOCK)
        except OSError:
            pass

    def close(self):
        os.close(self.fd)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _xfer(self, write: bytes = b"", read_len: int = 0) -> bytes:
        msgs = []
        keep = []
        if write:
            wb = (ctypes.c_uint8 * len(write)).from_buffer_copy(write)
            keep.append(wb)
            msgs.append(I2CMsg(self.addr, 0, len(write), wb))
        rb = None
        if read_len:
            rb = (ctypes.c_uint8 * read_len)()
            keep.append(rb)
            msgs.append(I2CMsg(self.addr, 1, read_len, rb))
        arr = (I2CMsg * len(msgs))(*msgs)
        data = I2CRdwrData(arr, len(msgs))
        fcntl.ioctl(self.fd, I2C_RDWR, data)
        return bytes(rb) if rb is not None else b""

    def write_block(self, byte_offset: int, data: bytes) -> None:
        """WRITE MEMORY writes exactly 4 bytes (one EEPROM block)."""
        if len(data) != BLOCK:
            raise ValueError("NTAG 5 EEPROM writes must be exactly 4 bytes")
        if byte_offset % BLOCK:
            raise ValueError("byte offset must be 4-byte aligned")
        if not 0 <= byte_offset < USER_SIZE:
            raise ValueError("write outside the 2048-byte user memory")
        block = byte_offset // BLOCK
        self._xfer(bytes([(block >> 8) & 0xFF, block & 0xFF]) + data)
        # EEPROM write cycle; the tag NAKs reads while it is still busy.
        time.sleep(0.01)

    def write(self, byte_offset: int, data: bytes) -> None:
        if byte_offset % BLOCK or len(data) % BLOCK:
            raise ValueError("offset and length must be 4-byte aligned")
        for i in range(0, len(data), BLOCK):
            self.write_block(byte_offset + i, data[i:i + BLOCK])

    def dump(self, size: int = USER_SIZE) -> bytes:
        if size % BLOCK:
            raise ValueError("size must be 4-byte aligned")
        out = bytearray()
        for off in range(0, size, 0x100):
            out += self.read_bytes(off, min(0x100, size - off))
        return bytes(out)


# ---------------------------------------------------------------------------
# NDEF


@dataclass
class NdefRecord:
    tnf: int
    type: bytes
    payload: bytes
    id: bytes = b""

    def marshal(self) -> bytes:
        short = len(self.payload) < 256
        flags = 0xD0 if short else 0xC0  # MB | ME | (SR)
        flags |= self.tnf & 0x07
        if self.id:
            flags |= 0x08
        out = bytearray([flags, len(self.type)])
        if short:
            out.append(len(self.payload))
        else:
            out += struct.pack(">I", len(self.payload))
        if self.id:
            out.append(len(self.id))
        out += self.type
        if self.id:
            out += self.id
        out += self.payload
        return bytes(out)


def parse_ndef_message(data: bytes) -> list[NdefRecord]:
    records = []
    i = 0
    while i < len(data):
        h = data[i]
        i += 1
        if h == 0:
            break
        mb, me, cf, sr, il, tnf = (
            h >> 7, (h >> 6) & 1, (h >> 5) & 1, (h >> 4) & 1, (h >> 3) & 1, h & 7
        )
        if cf:
            raise ValueError("chunked NDEF records are not supported")
        tlen = data[i]
        i += 1
        if sr:
            plen = data[i]
            i += 1
        else:
            plen = struct.unpack(">I", data[i:i + 4])[0]
            i += 4
        ilen = data[i] if il else 0
        if il:
            i += 1
        rtype = data[i:i + tlen]
        i += tlen
        rid = data[i:i + ilen]
        i += ilen
        payload = data[i:i + plen]
        i += plen
        records.append(NdefRecord(tnf, rtype, payload, rid))
        if me:
            break
    return records


def build_ndef_message(records: list[NdefRecord]) -> bytes:
    return b"".join(r.marshal() for r in records)


def read_ndef(mem: bytes) -> tuple[bytes, list[NdefRecord]]:
    """Return (raw NDEF message, records) from the tag's user memory."""
    cc = mem[0:4]
    if cc[0] != 0xE1:
        raise ValueError(f"unexpected capability container: {cc.hex()}")
    i = 4
    while i < len(mem):
        tlv = mem[i]
        i += 1
        if tlv == 0x00:  # NULL TLV
            continue
        if tlv == 0xFE:  # Terminator TLV
            break
        if i >= len(mem):
            break
        length = mem[i]
        i += 1
        if length == 0xFF:
            length = struct.unpack(">H", mem[i:i + 2])[0]
            i += 2
        value = mem[i:i + length]
        i += length
        if tlv == 0x03:
            return value, parse_ndef_message(value)
    raise ValueError("no NDEF TLV found")


def ndef_capacity(mem: bytes) -> int:
    """NDEF capacity from the Type 5 capability container (size field * 8)."""
    if mem[0] != 0xE1:
        raise ValueError(f"unexpected capability container: {mem[0:4].hex()}")
    return mem[2] * 8


def build_tlv(message: bytes) -> bytes:
    tlv = b"\x03"
    if len(message) < 0x100:
        tlv += bytes([len(message)])
    elif len(message) < 0x10000:
        tlv += b"\xff" + struct.pack(">H", len(message))
    else:
        raise ValueError("NDEF message too large")
    return tlv + message + b"\xfe"


def write_ndef(mem: bytes, message: bytes) -> bytes:
    """Return a new user-memory image with the NDEF TLV replaced."""
    if len(mem) < USER_SIZE:
        mem = mem.ljust(USER_SIZE, b"\xff")
    cc = mem[0:4]
    cap = ndef_capacity(mem)
    tlv = build_tlv(message)
    body = cc + tlv
    if len(body) > cap:
        raise ValueError(
            f"NDEF too large: {len(body)} > {cap} bytes advertised by the tag"
        )
    return body.ljust(USER_SIZE, b"\xff")


# ---------------------------------------------------------------------------
# Xiaomi MI-NFCTAG payload

XIAOMI_TYPE = b"com.xiaomi.mi_connect_service:externaltype"


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = 0
    val = 0
    while True:
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, i
        shift += 7


def parse_protobuf(buf: bytes) -> dict[int, list]:
    """Small protobuf wire-format parser: field -> list of values."""
    out: dict[int, list] = {}
    i = 0
    while i < len(buf):
        key, i = _read_varint(buf, i)
        field_no, wire = key >> 3, key & 7
        if wire == 0:
            val, i = _read_varint(buf, i)
        elif wire == 1:
            val = buf[i:i + 8]
            i += 8
        elif wire == 2:
            ln, i = _read_varint(buf, i)
            val = buf[i:i + ln]
            i += ln
        elif wire == 5:
            val = buf[i:i + 4]
            i += 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        out.setdefault(field_no, []).append(val)
    return out


def decode_xiaomi(payload: bytes) -> dict:
    top = parse_protobuf(payload)
    adv_raw = top.get(1, [b""])[0]
    adv = parse_protobuf(adv_raw)
    app_ids = [v for v in adv.get(13, []) if isinstance(v, int)]
    app_ids += [v for v in adv.get(3, []) if isinstance(v, int)]
    res = {
        "versionMajor": adv.get(1, [0])[0],
        "versionMinor": adv.get(2, [0])[0],
        "name": adv.get(5, [b""])[0].decode("utf-8", "replace"),
        "deviceType": adv.get(7, [0])[0],
        "flags": adv.get(4, [b""])[0].hex(),
        "appIds": app_ids,
        "btMac": adv.get(17, [b""])[0].decode("ascii", "replace"),
        "wifiMac": adv.get(12, [b""])[0].decode("ascii", "replace"),
        "wiredMac": adv.get(16, [b""])[0].decode("ascii", "replace"),
        "appsData": [v.hex() for v in adv.get(9, [])],
    }
    if app_ids:
        res["appNames"] = [APP_NAMES.get(a, f"0x{a:04x}") for a in app_ids]
    for hexdata in res["appsData"]:
        try:
            res["appData"] = parse_app_data(bytes.fromhex(hexdata))
        except Exception as e:  # noqa: BLE001
            res["appDataError"] = str(e)
    return res


DEVICE_TYPES = {2: "TV", 3: "PC", 5: "CAR", 8: "PAD", 15: "MI-NFCTAG"}


def parse_app_data(data: bytes) -> dict:
    """Decode Xiaomi's appData blob (seen: 27 17 00 00 00 03 00 0e ...)."""
    out: dict = {"raw": data.hex()}
    if len(data) < 8:
        return out
    out["prefix"] = data[0:2].hex()
    out["deviceType"] = data[5]
    out["deviceTypeName"] = DEVICE_TYPES.get(data[5], "unknown")
    i = 7
    if i < len(data):
        ln = data[i]
        i += 1
        out["action"] = data[i:i + ln].decode("ascii", "replace")
        i += ln
    while i + 1 < len(data):
        tag = data[i]
        ln = data[i + 1]
        i += 2
        val = data[i:i + ln]
        i += ln
        if tag == 0x65:
            out["action2"] = val.decode("ascii", "replace")
        elif tag == 0x01:
            out["btMac"] = val.decode("ascii", "replace")
        else:
            out[f"tag_{tag:02x}"] = val.hex()
    return out


APP_NAMES = {
    1: "MI_SHARE",
    2: "MI_PLAY",
    3: "MI_MIRROR",
    62: "MI_ACCOUNT",
    63: "MI_MOVER",
    16376: "MI_WATCH_CAMERA",
    16377: "MI_VIDEO_RELAY",
    16378: "MI_TAP",
    16379: "MI_VOIP",
    16381: "MI_REMOTE_CONTROLLER",
    16382: "MI_TEST",
}


# ---------------------------------------------------------------------------


def cmd_info(args):
    with Ntag5(args.bus, args.addr) as tag:
        head = tag.read_bytes(0, 256)
    cc = head[0:4]
    print(f"bus: {args.bus}  address: 0x{args.addr:02x}")
    print(f"capability container: {cc.hex()}")
    if cc[0] == 0xE1:
        print(f"  magic: 0x{cc[0]:02x} (NFC Forum TLV)")
        print(f"  version: 0x{cc[1]:02x} (major {cc[1] >> 4}, minor {cc[1] & 0xf})")
        print(f"  NDEF capacity: {cc[2] * 8} bytes")
        print(f"  access: 0x{cc[3]:02x}")
    try:
        raw, records = read_ndef(head)
        print(f"  NDEF: {len(raw)} bytes, {len(records)} record(s)")
        for rec in records:
            print(f"    tnf={rec.tnf} type={rec.type.decode('utf-8', 'replace')!r} "
                  f"payload={len(rec.payload)} bytes")
    except Exception as e:  # noqa: BLE001
        print(f"  NDEF parse: {e}")


def cmd_scratch(args):
    """Write/read/restore one block outside the NDEF area."""
    off = args.offset
    with Ntag5(args.bus, args.addr) as tag:
        old = tag.read_bytes(off, BLOCK)
        print(f"old  {off:04x}: {old.hex()}")
        try:
            tag.write_block(off, b"\xa5\x5a\xc3\x3c")
            new = tag.read_bytes(off, BLOCK)
            print(f"test {off:04x}: {new.hex()}")
        finally:
            tag.write_block(off, old)
        restored = tag.read_bytes(off, BLOCK)
        print(f"back {off:04x}: {restored.hex()}")
    if new == b"\xa5\x5a\xc3\x3c" and restored == old:
        print("scratch write/read/restore OK")
    else:
        raise SystemExit("scratch test FAILED")


def cmd_dump(args):
    with Ntag5(args.bus, args.addr) as tag:
        mem = tag.dump(args.size)
    out = Path(args.output or f"ntag5-{datetime.now():%Y%m%d-%H%M%S}.bin")
    out.write_bytes(mem)
    print(f"dumped {len(mem)} bytes -> {out}")
    for off in range(0, min(len(mem), 0x100), 16):
        chunk = mem[off:off + 16]
        print(f"{off:04x}: " + " ".join(f"{b:02x}" for b in chunk))


def cmd_read(args):
    with Ntag5(args.bus, args.addr) as tag:
        mem = tag.dump(args.size) if args.size else tag.dump()
    if args.hex:
        for off in range(0, len(mem), 16):
            chunk = mem[off:off + 16]
            print(f"{off:04x}: " + " ".join(f"{b:02x}" for b in chunk))
        return
    raw, records = read_ndef(mem)
    print(f"NDEF message: {len(raw)} bytes")
    for n, rec in enumerate(records, 1):
        print(f"\nrecord {n}:")
        print(f"  tnf: {rec.tnf}")
        print(f"  type: {rec.type.decode('utf-8', 'replace')}")
        print(f"  payload: {rec.payload.hex()}")
        if rec.tnf == 4 and rec.type == XIAOMI_TYPE:
            try:
                info = decode_xiaomi(rec.payload)
                print("  xiaomi MI-NFCTAG:")
                for k, v in info.items():
                    print(f"    {k}: {v}")
            except Exception as e:  # noqa: BLE001
                print(f"  (xiaomi payload parse failed: {e})")


def cmd_restore(args):
    data = Path(args.file).read_bytes()
    if len(data) > USER_SIZE:
        raise SystemExit("file is larger than the tag user memory")
    data = data.ljust(USER_SIZE, b"\xff")
    # Block 0 holds the capability container and the last block is the
    # NFC-only counter block; neither is written back.
    with Ntag5(args.bus, args.addr) as tag:
        current = tag.dump()
        end = USER_SIZE - BLOCK if args.full else min(ndef_capacity(current),
                                                      USER_SIZE - BLOCK)
        start = BLOCK
        changed = [
            off for off in range(start, end, BLOCK)
            if current[off:off + BLOCK] != data[off:off + BLOCK]
        ]
        failed = []
        for off in changed:
            try:
                tag.write_block(off, data[off:off + BLOCK])
            except OSError:
                failed.append(off)
        bad = []
        for off in changed:
            tag.wake()
            if tag.read_bytes(off, BLOCK) != data[off:off + BLOCK]:
                bad.append(off)
        if bad:
            # Locked / OTP blocks outside the NDEF TLV are common.  Only fail
            # when the NDEF area itself did not come back.
            ndef_bad = [b for b in bad if b < 1024]
            print(f"{len(bad)} block(s) outside the writable NDEF area differ "
                  f"(first 0x{bad[0]:04x}); they are left as-is")
            if ndef_bad:
                raise SystemExit(f"restore failed inside NDEF area at "
                                 f"0x{ndef_bad[0]:04x}")
    print(f"wrote {len(changed)} changed block(s), {len(failed)} NAKed")
    print(f"restored {start}..0x{end:x} from {args.file}")


def cmd_write_vcard(args):
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"FN:{args.name}",
        f"N:{args.name};;;;",
    ]
    if args.phone:
        lines.append(f"TEL;TYPE=CELL:{args.phone}")
    if args.email:
        lines.append(f"EMAIL:{args.email}")
    lines.append("END:VCARD")
    payload = "\r\n".join(lines).encode()
    _write_message(args, build_ndef_message([NdefRecord(2, b"text/vcard", payload)]))


def _wsc_attr(kind: int, value: bytes) -> bytes:
    return struct.pack(">HH", kind, len(value)) + value


def cmd_write_wifi(args):
    auth_name = args.auth.upper()
    auth = {
        "OPEN": 0x0001, "WPA": 0x0002, "WPA2": 0x0020,
        "WPA-WPA2": 0x0022, "WPA2-WPA": 0x0022,
    }.get(auth_name, 0x0020)
    enc = 0x0001 if auth == 0x0001 else (0x0008 if "WPA2" in auth_name else 0x0004)
    attrs = b"".join([
        _wsc_attr(0x1045, args.ssid.encode()),
        _wsc_attr(0x1003, struct.pack(">H", auth)),
        _wsc_attr(0x100F, struct.pack(">H", enc)),
        _wsc_attr(0x1027, args.password.encode()),
    ])
    payload = _wsc_attr(0x100E, attrs)  # Credential
    _write_message(args, build_ndef_message([
        NdefRecord(2, b"application/vnd.wfa.wsc", payload)
    ]))


def cmd_watch(args):
    last = None
    with Ntag5(args.bus, args.addr) as tag:
        print(f"watching {args.bus} 0x{args.addr:02x} every {args.interval}s")
        while True:
            try:
                mem = tag.dump()
                raw, records = read_ndef(mem)
            except Exception as e:  # noqa: BLE001
                print(f"read error: {e}")
                time.sleep(args.interval)
                continue
            if raw != last:
                stamp = time.strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{stamp}] NDEF changed, {len(raw)} bytes")
                for rec in records:
                    print(f"  tnf={rec.tnf} type={rec.type.decode('utf-8', 'replace')!r}")
                    if rec.tnf == 1 and rec.type == b"U":
                        prefix = {
                            0x00: "", 0x01: "http://www.", 0x02: "https://www.",
                            0x03: "http://", 0x04: "https://", 0x05: "tel:",
                            0x06: "mailto:",
                        }.get(rec.payload[0], "")
                        print("  uri:", prefix + rec.payload[1:].decode("utf-8", "replace"))
                    else:
                        print("  payload:", rec.payload.hex())
                last = raw
                if args.exec:
                    subprocess.run(args.exec, shell=True, check=False)
            time.sleep(args.interval)


def cmd_write_text(args):
    rec = NdefRecord(1, b"T", b"\x02en" + args.text.encode())
    _write_message(args, build_ndef_message([rec]))


def cmd_write_uri(args):
    prefixes = {
        "": 0x00, "http://www.": 0x01, "https://www.": 0x02,
        "http://": 0x03, "https://": 0x04, "tel:": 0x05, "mailto:": 0x06,
    }
    prefix, rest = 0x00, args.uri
    for p, code in prefixes.items():
        if p and args.uri.startswith(p):
            prefix, rest = code, args.uri[len(p):]
            break
    rec = NdefRecord(1, b"U", bytes([prefix]) + rest.encode())
    _write_message(args, build_ndef_message([rec]))


def cmd_write_raw(args):
    _write_message(args, bytes.fromhex(args.hex))


def _ndfc_end(mem: bytes) -> int:
    """Byte offset just past the NDEF TLV (including its terminator)."""
    i = 4
    while i < len(mem):
        tlv = mem[i]
        i += 1
        if tlv == 0:
            continue
        if tlv == 0xFE:
            return i
        if i >= len(mem):
            break
        ln = mem[i]
        i += 1
        if ln == 0xFF:
            ln = struct.unpack(">H", mem[i:i + 2])[0]
            i += 2
        i += ln
        if tlv == 0x03:
            return i
    return 4


def _write_message(args, message: bytes):
    with Ntag5(args.bus, args.addr) as tag:
        mem = tag.dump()
    new = write_ndef(mem, message)
    end = max(_ndfc_end(mem), 4 + len(build_tlv(message)))
    end = (end + BLOCK - 1) // BLOCK * BLOCK
    if end > ndef_capacity(mem):
        raise SystemExit("new NDEF does not fit in the tag's NDEF area")
    print(f"NDEF message: {len(message)} bytes, writing {end} bytes")
    print("tag NDEF capacity:", ndef_capacity(mem), "bytes")
    if args.dry_run:
        print("dry run; nothing written")
        return
    with Ntag5(args.bus, args.addr) as tag:
        # Leave the capability container (block 0) alone; it may be locked.
        # Only the TLV area from block 1 is rewritten, and bytes past the
        # terminator keep their old value.
        part = new[4:end]
        tag.write(4, part)
        verify = tag.read_bytes(4, len(part))
    if verify != part:
        raise SystemExit("verify failed: tag content differs after write")
    print("written and verified")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-b", "--bus", default=BUS_DEFAULT)
    ap.add_argument("-a", "--addr", type=lambda x: int(x, 0), default=ADDR_DEFAULT)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="show capability container and NDEF summary")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("scratch", help="write/read/restore one block outside NDEF")
    p.add_argument("-o", "--offset", type=lambda x: int(x, 0), default=0x7E0)
    p.set_defaults(func=cmd_scratch)

    p = sub.add_parser("dump", help="dump user EEPROM to a file")
    p.add_argument("-o", "--output")
    p.add_argument("-s", "--size", type=lambda x: int(x, 0), default=USER_SIZE)
    p.set_defaults(func=cmd_dump)

    p = sub.add_parser("read", help="read and parse the NDEF message")
    p.add_argument("--hex", action="store_true", help="dump raw memory instead")
    p.add_argument("-s", "--size", type=lambda x: int(x, 0), default=0)
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("restore", help="write a dump back")
    p.add_argument("file")
    p.add_argument("--full", action="store_true",
                   help="also restore the random tail outside the NDEF area")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("write-text", help="write an NDEF text record")
    p.add_argument("text")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_write_text)

    p = sub.add_parser("write-uri", help="write an NDEF URI record")
    p.add_argument("uri")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_write_uri)

    p = sub.add_parser("write-raw", help="write a raw NDEF message (hex)")
    p.add_argument("hex")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_write_raw)
    p = sub.add_parser("write-vcard", help="write an NDEF vCard record")
    p.add_argument("--name", required=True)
    p.add_argument("--phone")
    p.add_argument("--email")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_write_vcard)

    p = sub.add_parser("write-wifi", help="write an NDEF Wi-Fi credential record")
    p.add_argument("--ssid", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--auth", default="WPA2",
                   choices=["OPEN", "WPA", "WPA2", "WPA-WPA2"])
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_write_wifi)

    p = sub.add_parser("watch", help="poll the tag and print/act on NDEF changes")
    p.add_argument("--interval", type=float, default=1.0)
    p.add_argument("--exec", help="shell command to run after a change")
    p.set_defaults(func=cmd_watch)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
