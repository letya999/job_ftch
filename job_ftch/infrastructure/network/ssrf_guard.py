"""Transport-level SSRF guard for infrastructure HTTP clients."""

from __future__ import annotations

import asyncio
import ipaddress
import socket

import httpx

_IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
# RFC 2606 documentation names: public, never resolved for SSRF classification.
_RESERVED_PUBLIC_HOSTS = frozenset({"example.com", "example.net", "example.org"})
_PRIVATE_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
    ipaddress.ip_network("::ffff:0:0/96"),
)


def _is_private_ip(addr: _IpAddress) -> bool:
    mapped = getattr(addr, "ipv4_mapped", None)
    return any((mapped or addr) in network for network in _PRIVATE_NETWORKS)


def _is_reserved_public_host(host: str) -> bool:
    lowered = host.strip(".").casefold()
    return lowered in _RESERVED_PUBLIC_HOSTS or any(
        lowered.endswith(f".{name}") for name in _RESERVED_PUBLIC_HOSTS
    )


def _host_is_private(host: str) -> bool:
    if not host or _is_reserved_public_host(host):
        return False
    try:
        return _is_private_ip(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, OSError, UnicodeError, RuntimeError):
        return False
    return any(_is_private_ip(ipaddress.ip_address(sockaddr[0])) for *_unused, sockaddr in infos)


async def check_ssrf(url: str) -> None:
    host = httpx.URL(url).host or ""
    is_private = await asyncio.to_thread(_host_is_private, host)
    if host and is_private:
        raise httpx.LocalProtocolError(f"SSRF guard blocked request to private host {host!r}")


class SSRFGuardedTransport(httpx.AsyncBaseTransport):
    def __init__(self, wrapped: httpx.AsyncBaseTransport, allowlist: tuple[str, ...] = ()) -> None:
        self._wrapped = wrapped
        self._allowlist = frozenset(allowlist)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host or ""
        is_private = await asyncio.to_thread(_host_is_private, host)
        if host not in self._allowlist and is_private:
            raise httpx.LocalProtocolError(f"SSRF guard blocked request to private host {host!r}")
        return await self._wrapped.handle_async_request(request)

    async def aclose(self) -> None:
        await self._wrapped.aclose()
