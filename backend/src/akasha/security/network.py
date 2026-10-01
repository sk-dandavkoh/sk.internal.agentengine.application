"""Outbound network guard for user-configured destinations (SSRF).

Nodes that connect to a host a *user* configured (core.http, core.smtp)
check it here first: every address the name resolves to must be globally
routable, unless the operator explicitly allows private networks (e.g. a
self-hosted install talking to its own LAN, or tests against 127.0.0.1).
Loopback, RFC 1918, link-local (cloud metadata 169.254.169.254), CGNAT,
multicast, reserved and unspecified addresses are therefore blocked by
default — also when reached through a DNS name or an IPv4-mapped IPv6
address.

The name is resolved once: ``check_destination`` returns the vetted
addresses and callers connect to exactly those (TLS still verifies the
name), so a DNS-rebinding server cannot answer differently at connect time.
"""

import asyncio
import ipaddress
import socket
from dataclasses import dataclass

from akasha.domain.node import NodeError, NodeErrorKind


@dataclass(frozen=True)
class NetworkPolicy:
    """``allowed_hosts``: host names/IP literals the operator trusts even
    though they resolve to private addresses (e.g. one internal API),
    compared case-insensitively. Each redirect hop is checked on its own."""

    allow_private_networks: bool = False
    allowed_hosts: frozenset[str] = frozenset()


def _blocked(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])  # drop an IPv6 scope id
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        # Recent Pythons classify ::ffff:a.b.c.d by its IPv4 part already;
        # older 3.12 releases did not. Unwrap explicitly either way.
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast


async def check_destination(
    host: str, port: int, policy: NetworkPolicy
) -> tuple[str, ...]:
    """The addresses ``host:port`` may be contacted at, in resolver order;
    raise ``NodeError`` unless every one of them is allowed.

    The error names the host but never the resolved addresses, so the guard
    cannot be used to map internal DNS.
    """
    if not host:
        raise NodeError(
            "the destination has no host",
            kind=NodeErrorKind.CONFIGURATION,
            code="DESTINATION_INVALID",
        )
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        temporary = exc.errno == getattr(socket, "EAI_AGAIN", None)
        raise NodeError(
            f"cannot resolve host {host!r}",
            kind=NodeErrorKind.TRANSIENT if temporary else NodeErrorKind.PERMANENT,
            code="DNS_ERROR",
            details={"host": host},
        ) from None
    addresses = tuple(dict.fromkeys(str(info[4][0]) for info in infos))
    if policy.allow_private_networks or host.lower() in {
        h.lower() for h in policy.allowed_hosts
    }:
        return addresses
    if any(_blocked(address) for address in addresses):
        raise NodeError(
            f"destination {host!r} is not a public address",
            kind=NodeErrorKind.PERMANENT,
            code="DESTINATION_BLOCKED",
            details={"host": host},
        )
    return addresses
