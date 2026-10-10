"""Public hostname for a game night.

A real domain is used when one is set. Otherwise the public IPv4 becomes a
sslip.io name so Caddy can get a certificate without a purchased domain.
"""

from __future__ import annotations

import ipaddress


def hostname_for(public_ip: str, domain: str | None = None) -> str:
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


def https_origin(hostname: str) -> str:
    host = hostname.strip().rstrip(".")
    if not host or "://" in host or "/" in host:
        raise ValueError("hostname must not include a scheme or path")
    return f"https://{host}"
