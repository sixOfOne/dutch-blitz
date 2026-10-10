"""Public hostname for a game night.

A Route 53 name is used when ``dns_zone_name`` is set. Otherwise a manual
domain is used when one is set. Otherwise the public IPv4 becomes a sslip.io
name so Caddy can get a certificate without a purchased domain.
"""

from __future__ import annotations

import ipaddress
import re

_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
# Relative names already published by hand for Clerk. Never select them.
RESERVED_DNS_LABELS = frozenset({"clerk", "accounts", "clkmail"})
DEFAULT_DNS_LABEL = "blitz"


def hostname_for(
    public_ip: str,
    domain: str | None = None,
    *,
    dns_zone_name: str | None = None,
    dns_record_name: str | None = None,
) -> str:
    fqdn = dns_fqdn(dns_zone_name, dns_record_name)
    if fqdn:
        return fqdn
    cleaned = (domain or "").strip().rstrip(".")
    if cleaned:
        if "://" in cleaned or "/" in cleaned or any(character.isspace() for character in cleaned):
            raise ValueError("domain must be a hostname, not a URL")
        return cleaned
    ip_text = (public_ip or "").strip()
    try:
        parsed = ipaddress.ip_address(ip_text)
    except ValueError as exc:
        raise ValueError(f"public IP is not valid: {ip_text!r}") from exc
    if parsed.version != 4:
        raise ValueError("sslip.io hostname needs an IPv4 address")
    return f"{parsed}.sslip.io"


def dns_fqdn(zone_name: str | None, record_name: str | None = None) -> str | None:
    """``blitz.sixofonedev.com`` when a zone is set, otherwise ``None``."""
    zone = (zone_name or "").strip().rstrip(".").lower()
    if not zone:
        return None
    if record_name is None:
        label = DEFAULT_DNS_LABEL
    else:
        label = record_name.strip().rstrip(".").lower()
    _validate_zone(zone)
    _validate_label(label)
    return f"{label}.{zone}"


def https_origin(hostname: str) -> str:
    host = hostname.strip().rstrip(".")
    if not host or "://" in host or "/" in host:
        raise ValueError("hostname must not include a scheme or path")
    return f"https://{host}"


def _validate_zone(zone: str) -> None:
    if "://" in zone or "/" in zone or any(character.isspace() for character in zone):
        raise ValueError("dns_zone_name must be a domain name, not a URL")
    labels = zone.split(".")
    if len(labels) < 2 or not all(_LABEL.fullmatch(label) for label in labels):
        raise ValueError("dns_zone_name must be a domain such as sixofonedev.com")


def _validate_label(label: str) -> None:
    if not _LABEL.fullmatch(label):
        raise ValueError("dns_record_name must be a single label such as blitz")
    if label in RESERVED_DNS_LABELS:
        raise ValueError(f"dns_record_name {label} is reserved for an existing Clerk record")
