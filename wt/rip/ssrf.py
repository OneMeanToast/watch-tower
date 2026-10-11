"""Refuse private, loopback, and link-local targets, including after redirects."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

class SsrfError(ValueError):
    pass


_SUFFIXES = (".local", ".localhost", ".test", ".invalid", ".internal")


def guard_url(url: str, *, resolver=None) -> str:
    """Return the URL when every resolved address is public. Otherwise raise."""
    parts = urlsplit((url or "").strip())
    if parts.scheme not in {"http", "https"}:
        raise SsrfError(f"only http and https can be ripped ({parts.scheme or 'missing scheme'})")
    host = parts.hostname
    if not host:
        raise SsrfError("URL has no host")
    host_l = host.lower().rstrip(".")
    if host_l == "localhost" or host_l.endswith(_SUFFIXES) or host_l.isdigit():
        raise SsrfError(f"refusing non-public host {host_l}")
    try:
        ipaddress.ip_address(host_l)
        ips = [host_l]
    except ValueError:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        ips = _resolve(host_l, port, resolver)
    for ip in ips:
        if _blocked_ip(ip):
            raise SsrfError(f"refusing non-public address {ip}")
    return url


def _resolve(host: str, port: int, resolver) -> list[str]:
    fn = resolver or socket.getaddrinfo
    try:
        infos = fn(host, port)
    except socket.gaierror as exc:
        raise SsrfError(f"could not resolve {host}") from exc
    ips: list[str] = []
    for info in infos or []:
        if isinstance(info, tuple) and len(info) >= 5 and isinstance(info[4], tuple):
            ips.append(str(info[4][0]))
        elif isinstance(info, tuple) and info:
            ips.append(str(info[0]))
        else:
            ips.append(str(info))
    if not ips:
        raise SsrfError(f"could not resolve {host}")
    return ips


def _blocked_ip(value: str) -> bool:
    try:
        addr = ipaddress.ip_address(value.split("%")[0])
    except ValueError:
        return True
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    ):
        return True
    if isinstance(addr, ipaddress.IPv4Address) and addr in ipaddress.ip_network("100.64.0.0/10"):
        return True
    return False
