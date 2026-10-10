"""Wait until a game-night A record is the new instance address.

Route 53 answers immediately. Public resolvers can still return the previous
address until the 60 second TTL expires, so Caddy waits for both.
"""

from __future__ import annotations

import ipaddress
import random
import socket
import struct
import time

PUBLIC_RESOLVERS = ("1.1.1.1", "8.8.8.8")
DNS_WAIT_TIMEOUT_SECONDS = 180
DNS_WAIT_INTERVAL_SECONDS = 5
_QUERY_TIMEOUT_SECONDS = 5.0
_QTYPE_A = 1
_QTYPE_NS = 2


class DnsTruncated(OSError):
    """The UDP answer set the TC bit."""


def a_records_match(records: list[str], expected_ip: str) -> bool:
    """True when the answer is exactly the new address and no other."""
    try:
        expected = str(ipaddress.IPv4Address(expected_ip.strip()))
        got = {str(ipaddress.IPv4Address(item.strip())) for item in records}
    except (ipaddress.AddressValueError, ValueError):
        return False
    return got == {expected}


def encode_name(name: str) -> bytes:
    labels = name.strip().rstrip(".").split(".")
    encoded = bytearray()
    for label in labels:
        raw = label.encode("ascii")
        if not raw or len(raw) > 63:
            raise ValueError(f"dns name is not valid: {name!r}")
        encoded.append(len(raw))
        encoded.extend(raw)
    encoded.append(0)
    return bytes(encoded)


def decode_name(packet: bytes, offset: int) -> tuple[str, int]:
    labels: list[str] = []
    jumped = False
    end = offset
    seen: set[int] = set()
    while True:
        if offset >= len(packet):
            raise OSError("truncated dns name")
        length = packet[offset]
        if length == 0:
            if not jumped:
                end = offset + 1
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise OSError("truncated dns pointer")
            pointer = ((length & 0x3F) << 8) | packet[offset + 1]
            if pointer in seen or pointer >= len(packet):
                raise OSError("dns compression pointer is invalid")
            seen.add(pointer)
            if not jumped:
                end = offset + 2
            jumped = True
            offset = pointer
            continue
        if length & 0xC0:
            raise OSError("unsupported dns label")
        offset += 1
        if offset + length > len(packet):
            raise OSError("truncated dns label")
        labels.append(packet[offset : offset + length].decode("ascii"))
        offset += length
        if not jumped:
            end = offset
    return ".".join(labels), end


def build_query(name: str, qtype: int, *, recursive: bool, ident: int) -> bytes:
    flags = 0x0100 if recursive else 0
    header = struct.pack("!HHHHHH", ident, flags, 1, 0, 0, 0)
    return header + encode_name(name) + struct.pack("!HH", qtype, 1)


def decode_records(packet: bytes, expected_id: int, qtype: int) -> list[str]:
    """Answers of ``qtype`` from one DNS message. NXDOMAIN is an empty list."""
    if len(packet) < 12:
        raise OSError("short dns packet")
    ident, flags, qdcount, ancount = struct.unpack("!HHHH", packet[:8])
    if ident != expected_id:
        raise OSError("dns transaction id mismatch")
    rcode = flags & 0x000F
    if flags & 0x0200:
        raise DnsTruncated("truncated dns response")
    if rcode == 3:
        return []
    if rcode != 0:
        raise OSError(f"dns rcode {rcode}")
    offset = 12
    for _ in range(qdcount):
        _, offset = decode_name(packet, offset)
        offset += 4
    found: list[str] = []
    for _ in range(ancount):
        _, offset = decode_name(packet, offset)
        if offset + 10 > len(packet):
            raise OSError("truncated dns record")
        rtype, _rclass, _ttl, rdlength = struct.unpack("!HHIH", packet[offset : offset + 10])
        offset += 10
        if offset + rdlength > len(packet):
            raise OSError("truncated dns rdata")
        rdata_at = offset
        offset += rdlength
        if rtype != qtype:
            continue
        if qtype == _QTYPE_A and rdlength == 4:
            found.append(socket.inet_ntoa(packet[rdata_at : rdata_at + 4]))
        elif qtype == _QTYPE_NS:
            name, _ = decode_name(packet, rdata_at)
            found.append(name.rstrip("."))
    return found


class UdpDnsClient:
    """DNS lookup over UDP, with a TCP retry when the answer is truncated."""

    def __init__(self, timeout: float = _QUERY_TIMEOUT_SECONDS) -> None:
        self.timeout = timeout

    def a_records(self, name: str, server: str, *, recursive: bool = True) -> list[str]:
        return self._query(name, server, _QTYPE_A, recursive=recursive)

    def ns_records(self, zone: str, server: str) -> list[str]:
        return self._query(zone, server, _QTYPE_NS, recursive=True)

    def _query(self, name: str, server: str, qtype: int, *, recursive: bool) -> list[str]:
        ident = random.randrange(0, 65536)
        message = build_query(name, qtype, recursive=recursive, ident=ident)
        try:
            packet = _exchange_udp(message, server, self.timeout)
            return decode_records(packet, ident, qtype)
        except DnsTruncated:
            packet = _exchange_tcp(message, server, self.timeout)
            return decode_records(packet, ident, qtype)


def wait_for_a_record(
    hostname: str,
    expected_ip: str,
    *,
    zone: str,
    timeout: float = DNS_WAIT_TIMEOUT_SECONDS,
    interval: float = DNS_WAIT_INTERVAL_SECONDS,
    public_resolvers: tuple[str, ...] = PUBLIC_RESOLVERS,
    client: object | None = None,
    sleep=time.sleep,
    clock=time.monotonic,
) -> bool:
    """Poll until authoritative servers and public resolvers all return ``expected_ip``."""
    resolver = client if client is not None else UdpDnsClient()
    deadline = clock() + timeout
    attempt = 0
    while True:
        attempt += 1
        if _name_matches(resolver, hostname, expected_ip, zone, public_resolvers):
            print(f"DNS for {hostname} is {expected_ip} (attempt {attempt}).")
            return True
        if clock() + interval > deadline:
            return False
        print(f"Waiting for {hostname} to resolve to {expected_ip} (attempt {attempt})...")
        sleep(interval)


def _name_matches(client: object, hostname: str, expected_ip: str, zone: str, public_resolvers: tuple[str, ...]) -> bool:
    nameservers = _authoritative_ips(client, zone, public_resolvers)
    if not nameservers:
        return False
    for server in nameservers:
        got = _safe_a(client, hostname, server, recursive=False)
        if got is None or not a_records_match(got, expected_ip):
            return False
    for server in public_resolvers:
        got = _safe_a(client, hostname, server, recursive=True)
        if got is None or not a_records_match(got, expected_ip):
            return False
    return True


def _authoritative_ips(client: object, zone: str, public_resolvers: tuple[str, ...]) -> list[str]:
    for resolver in public_resolvers:
        try:
            names = client.ns_records(zone, resolver)
        except OSError:
            continue
        addresses: list[str] = []
        for name in names:
            try:
                addresses.extend(client.a_records(name, resolver, recursive=True))
            except OSError:
                continue
        if addresses:
            return list(dict.fromkeys(addresses))
    return []


def _safe_a(client: object, name: str, server: str, *, recursive: bool) -> list[str] | None:
    try:
        return client.a_records(name, server, recursive=recursive)
    except OSError:
        return None


def _exchange_udp(message: bytes, server: str, timeout: float) -> bytes:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        sock.sendto(message, (server, 53))
        packet, _ = sock.recvfrom(4096)
        return packet
    finally:
        sock.close()


def _exchange_tcp(message: bytes, server: str, timeout: float) -> bytes:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect((server, 53))
        sock.sendall(struct.pack("!H", len(message)) + message)
        length = struct.unpack("!H", _recvall(sock, 2))[0]
        return _recvall(sock, length)
    finally:
        sock.close()


def _recvall(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        piece = sock.recv(size - len(chunks))
        if not piece:
            raise OSError("short dns tcp read")
        chunks.extend(piece)
    return bytes(chunks)
