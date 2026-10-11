"""Wait until a game-night A record is the new instance address.

Route 53 answers immediately. Public resolvers can still return the previous
address until the 60 second TTL expires, so Caddy waits for both.

The default path is DNS on port 53, which is what the Mac uses. Set
``DUTCH_BLITZ_DNS_MODE=doh`` when port 53 is intercepted: the wait then
uses the dns.google JSON API and falls back to Cloudflare. That HTTPS
path is also tried automatically when a classic lookup returns an address
in 198.18.0.0/15 or an empty answer. When the AWS CLI can read the hosted
zone, the HTTPS path also checks the A record with ListResourceRecordSets.
"""

from __future__ import annotations

import ipaddress
import json
import os
import random
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

PUBLIC_RESOLVERS = ("1.1.1.1", "8.8.8.8")
DNS_WAIT_TIMEOUT_SECONDS = 180
DNS_WAIT_INTERVAL_SECONDS = 5
DOH_ENDPOINTS = (
    "https://dns.google/resolve",
    "https://cloudflare-dns.com/dns-query",
)
_QUERY_TIMEOUT_SECONDS = 5.0
_QTYPE_A = 1
_QTYPE_NS = 2
_BENCHMARK = ipaddress.ip_network("198.18.0.0/15")


class DnsTruncated(OSError):
    """The UDP answer set the TC bit."""


def answers_unusable(records: list[str] | None) -> bool:
    """True for no answer, or any address in the 198.18.0.0/15 intercept range."""
    if not records:
        return True
    try:
        addresses = [ipaddress.ip_address(item.strip()) for item in records]
    except ValueError:
        return True
    return any(address in _BENCHMARK for address in addresses)


def resolve_dns_mode(mode: str | None = None) -> str:
    """``classic`` (default) or ``doh``. An unknown value is an error."""
    raw = mode if mode is not None else os.environ.get("DUTCH_BLITZ_DNS_MODE", "")
    value = raw.strip().lower()
    if value in ("", "classic"):
        return "classic"
    if value == "doh":
        return "doh"
    raise ValueError(f"DUTCH_BLITZ_DNS_MODE must be classic or doh, got {raw!r}.")


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


def a_records_from_doh(payload: object) -> list[str]:
    """A records from a dns.google or Cloudflare JSON response."""
    if not isinstance(payload, dict):
        raise OSError("doh response was not a JSON object")
    status = payload.get("Status")
    if status == 3:
        return []
    if status != 0:
        raise OSError(f"doh status {status}")
    found: list[str] = []
    for answer in payload.get("Answer") or []:
        if not isinstance(answer, dict):
            continue
        data = answer.get("data")
        if answer.get("type") == _QTYPE_A and isinstance(data, str):
            found.append(data)
    return found


def doh_a_records(
    hostname: str,
    *,
    fetch,
    timeout: float = _QUERY_TIMEOUT_SECONDS,
) -> list[str] | None:
    """A records from dns.google, or Cloudflare when that request fails.

    A real address from Google is returned as-is. An empty answer, a
    198.18.0.0/15 answer, or a transport error tries the next endpoint.
    None means every endpoint failed.
    """
    fallback: list[str] | None = None
    for endpoint in DOH_ENDPOINTS:
        url = endpoint + "?" + urllib.parse.urlencode({"name": hostname, "type": "A"})
        try:
            records = a_records_from_doh(fetch(url, timeout))
        except (OSError, ValueError, TypeError, urllib.error.URLError):
            continue
        if not answers_unusable(records):
            return records
        fallback = records
    return fallback


def _doh_http_json(url: str, timeout: float) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/dns-json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise OSError("doh response was not a JSON object")
    return payload


def _dns_name(value: str) -> str:
    return value.strip().rstrip(".").lower()


def _aws_json(argv: list[str], run) -> dict | None:
    try:
        completed = run(argv, capture_output=True, text=True, check=False)
    except OSError:
        return None
    if getattr(completed, "returncode", 1) != 0:
        return None
    try:
        payload = json.loads(completed.stdout or "")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _route53_zone_id(zone: str, run) -> str | None:
    payload = _aws_json(
        ["aws", "route53", "list-hosted-zones-by-name", "--dns-name", zone, "--output", "json"],
        run,
    )
    if payload is None:
        return None
    wanted = _dns_name(zone)
    for item in payload.get("HostedZones") or []:
        if not isinstance(item, dict):
            continue
        if _dns_name(str(item.get("Name", ""))) != wanted:
            continue
        config = item.get("Config") or {}
        if isinstance(config, dict) and config.get("PrivateZone"):
            continue
        zone_id = str(item.get("Id", "")).rsplit("/", 1)[-1]
        if zone_id:
            return zone_id
    return None


def _route53_record_sets(zone_id: str, run) -> list[dict] | None:
    records: list[dict] = []
    start_name = ""
    start_type = ""
    for _ in range(20):
        argv = [
            "aws",
            "route53",
            "list-resource-record-sets",
            "--hosted-zone-id",
            zone_id,
            "--output",
            "json",
        ]
        if start_name:
            argv.extend(["--start-record-name", start_name, "--start-record-type", start_type or "A"])
        payload = _aws_json(argv, run)
        if payload is None:
            return None
        for item in payload.get("ResourceRecordSets") or []:
            if isinstance(item, dict):
                records.append(item)
        if not payload.get("IsTruncated"):
            return records
        start_name = str(payload.get("NextRecordName") or "")
        start_type = str(payload.get("NextRecordType") or "")
        if not start_name:
            return records
    return records


def a_records_from_route53(hostname: str, record_sets: list[dict]) -> list[str]:
    """A values for ``hostname`` in a ListResourceRecordSets page."""
    wanted = _dns_name(hostname)
    values: list[str] = []
    for record in record_sets:
        if record.get("Type") != "A":
            continue
        if _dns_name(str(record.get("Name", ""))) != wanted:
            continue
        for item in record.get("ResourceRecords") or []:
            value = item.get("Value") if isinstance(item, dict) else None
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
    return values


def route53_a_records(
    hostname: str,
    zone: str,
    *,
    run=subprocess.run,
) -> list[str] | None:
    """A record from Route 53, or None when the lookup cannot be done.

    None leaves the DoH result to decide. An empty list means the zone
    was read and the name has no A record yet.
    """
    try:
        zone_id = _route53_zone_id(zone, run)
        if not zone_id:
            return None
        record_sets = _route53_record_sets(zone_id, run)
        if record_sets is None:
            return None
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    return a_records_from_route53(hostname, record_sets)


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
    mode: str | None = None,
    doh_fetch=None,
    route53_lookup=None,
) -> bool:
    """Poll until the name is ``expected_ip``.

    ``mode`` defaults to ``DUTCH_BLITZ_DNS_MODE`` (``classic`` when unset).
    Classic waits on the authoritative servers and on 1.1.1.1 and 8.8.8.8.
    ``doh`` skips port 53. A classic answer in 198.18.0.0/15, or an empty
    classic answer, tries DoH on its own.
    """
    selected = resolve_dns_mode(mode)
    resolver = client if client is not None else UdpDnsClient()
    fetch = doh_fetch if doh_fetch is not None else _doh_http_json
    lookup = route53_lookup if route53_lookup is not None else route53_a_records
    deadline = clock() + timeout
    attempt = 0
    while True:
        attempt += 1
        if selected == "doh":
            matched, confirmed = _doh_ready(hostname, expected_ip, zone, fetch, lookup)
            if matched:
                _print_doh_ready(hostname, expected_ip, attempt, confirmed)
                return True
        else:
            status = _classic_status(resolver, hostname, expected_ip, zone, public_resolvers)
            if status == "match":
                print(f"DNS for {hostname} is {expected_ip} (attempt {attempt}).")
                return True
            if status == "unusable":
                matched, confirmed = _doh_ready(hostname, expected_ip, zone, fetch, lookup)
                if matched:
                    _print_doh_ready(hostname, expected_ip, attempt, confirmed)
                    return True
        if clock() + interval > deadline:
            return False
        print(f"Waiting for {hostname} to resolve to {expected_ip} (attempt {attempt})...")
        sleep(interval)


def _print_doh_ready(hostname: str, expected_ip: str, attempt: int, confirmed: bool) -> None:
    via = "DoH and Route 53" if confirmed else "DoH"
    print(f"DNS for {hostname} is {expected_ip} via {via} (attempt {attempt}).")


def _classic_status(
    client: object,
    hostname: str,
    expected_ip: str,
    zone: str,
    public_resolvers: tuple[str, ...],
) -> str:
    """``match``, ``mismatch`` (a real other address), or ``unusable``.

    A real other address stops the scan. That is the Mac's stale-TTL
    case, and it stays on port 53. Empty answers and 198.18.0.0/15 are
    unusable only when no server returned a real address.
    """
    nameservers = _authoritative_ips(client, zone, public_resolvers)
    if not nameservers:
        return "unusable"
    saw_real = False
    saw_unusable = False
    for server in nameservers:
        got = _safe_a(client, hostname, server, recursive=False)
        if got is None or answers_unusable(got):
            saw_unusable = True
            continue
        saw_real = True
        if not a_records_match(got, expected_ip):
            return "mismatch"
    for server in public_resolvers:
        got = _safe_a(client, hostname, server, recursive=True)
        if got is None or answers_unusable(got):
            saw_unusable = True
            continue
        saw_real = True
        if not a_records_match(got, expected_ip):
            return "mismatch"
    # A real address that is not the new IP already returned. A mix of
    # the right address and an empty or intercepted answer stays on
    # port 53, so one slow resolver does not skip the Mac's check.
    if saw_unusable and not saw_real:
        return "unusable"
    if saw_unusable:
        return "mismatch"
    return "match"


def _doh_ready(hostname: str, expected_ip: str, zone: str, fetch, route53_lookup) -> tuple[bool, bool]:
    records = doh_a_records(hostname, fetch=fetch)
    if records is None or not a_records_match(records, expected_ip):
        return False, False
    try:
        authoritative = route53_lookup(hostname, zone)
    except OSError:
        authoritative = None
    if authoritative is None:
        return True, False
    if not a_records_match(authoritative, expected_ip):
        return False, False
    return True, True


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
