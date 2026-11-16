"""Which requests a browsing agent may make: GET and HEAD to public internet addresses only."""

import asyncio
import ipaddress
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

type IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
type Resolver = Callable[[str], Awaitable[list[str]]]
type AddressPolicy = Callable[[IpAddress], bool]

READ_METHODS = frozenset({"GET", "HEAD"})


async def system_resolver(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    return [str(info[4][0]) for info in infos]


def public_address(ip: IpAddress) -> bool:
    """True for internet addresses; loopback, private, link-local and reserved ranges are not."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global


class UrlGuard:
    """Keeps agents off the user's machine and network, where this service itself listens."""

    def __init__(
        self, resolve: Resolver = system_resolver, allowed: AddressPolicy = public_address
    ) -> None:
        self._resolve = resolve
        self._allowed = allowed
        self._hosts: dict[str, str | None] = {}

    async def request_problem(self, method: str, url: str) -> str | None:
        """Why a request must not leave the browser, or None when it may."""
        if method.upper() not in READ_METHODS:
            return f"{method} requests are blocked; browsing is read-only"
        return await self.url_problem(url)

    async def url_problem(self, url: str) -> str | None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            return f"only http and https pages can be opened, not {parts.scheme or 'this'!r}"
        host = parts.hostname
        if not host:
            return f"{url!r} has no host"
        if host not in self._hosts:
            self._hosts[host] = await self._host_problem(host)
        return self._hosts[host]

    def address_problem(self, address: str) -> str | None:
        """Checks where a response actually came from, which DNS can change after the URL check."""
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return f"unexpected server address {address!r}"
        return None if self._allowed(ip) else f"the page was served from private address {ip}"

    async def _host_problem(self, host: str) -> str | None:
        try:
            literal: IpAddress | None = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            addresses = [str(literal)]
        else:
            try:
                addresses = await self._resolve(host)
            except OSError as exc:
                return f"{host} does not resolve: {exc}"
        for address in addresses:
            problem = self.address_problem(address)
            if problem:
                logger.info("blocked host %s: %s", host, problem)
                return f"{host} is not on the public internet"
        return None
