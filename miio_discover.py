#!/usr/bin/env python3
"""Send the miIO discovery handshake and print replies."""
import socket
import struct
import sys

HELLO = bytes.fromhex(
    "21310020ffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
)


def parse(data: bytes) -> str:
    if len(data) < 32 or data[0:2] != b"\x21\x31":
        return f"unexpected {data.hex()}"
    length, unknown, did, stamp = struct.unpack(">HIII", data[2:16])
    token = data[16:32]
    return (f"len={length} unknown=0x{unknown:08x} did=0x{did:08x} "
            f"stamp={stamp} token={token.hex()}")


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(2.0)
    targets = ["255.255.255.255"]
    if len(sys.argv) > 1:
        targets += sys.argv[1:]
    for t in targets:
        for _ in range(3):
            s.sendto(HELLO, (t, 54321))
    seen = set()
    while True:
        try:
            data, addr = s.recvfrom(2048)
        except socket.timeout:
            break
        if addr[0] in seen:
            continue
        seen.add(addr[0])
        print(f"{addr[0]}:{addr[1]}  {parse(data)}")
    if not seen:
        print("no miIO reply")


if __name__ == "__main__":
    main()
