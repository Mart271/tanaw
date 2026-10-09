"""Loopback-only network guard.

Installed at startup so that nothing inside Tanaw's process can open a connection
to, or send a datagram to, anything other than this computer. It backs the
"nothing leaves the laptop" claim with code:

* ``socket.socket.connect`` / ``connect_ex`` / ``sendto`` refuse non-loopback peers.
* ``socket.getaddrinfo`` refuses to resolve hostnames other than ``localhost``, so a
  stray library cannot even leak a hostname through a DNS lookup.

Limits (stated honestly): this guards Python's ``socket`` module. Native code that
opens sockets without going through it (none of our dependencies do in the core
path) would not be covered. The Wi-Fi-off demo is the second line of evidence.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_LOCALHOST_NAMES = frozenset({"localhost", "localhost.", "ip6-localhost", "ip6-loopback"})

_lock = threading.Lock()
_originals: dict[str, Callable[..., Any]] = {}


class NetworkBlockedError(PermissionError):
    """Raised when code tries to reach a non-loopback address while the guard is on."""


def is_loopback_host(host: object) -> bool:
    """Return True if ``host`` (an IP literal or hostname) is this machine.

    Hostnames other than the well-known ``localhost`` names are treated as
    non-loopback without resolving them: we fail closed rather than do a lookup.
    """
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii")
        except UnicodeDecodeError:
            return False
    if not isinstance(host, str):
        return False
    name = host.strip().lower()
    if name in _LOCALHOST_NAMES:
        return True
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    # Strip an IPv6 zone id such as "fe80::1%eth0".
    name = name.split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped.is_loopback
    return ip.is_loopback


def is_allowed_address(family: int, address: object) -> bool:
    """Return True if a socket of ``family`` may connect/send to ``address``."""
    af_unix = getattr(socket, "AF_UNIX", None)
    if af_unix is not None and family == af_unix:
        return True  # Local IPC socket; never leaves the machine.
    if family in (socket.AF_INET, socket.AF_INET6):
        if isinstance(address, tuple) and len(address) >= 2:
            return is_loopback_host(address[0])
        return False
    # Unknown address families (Bluetooth, raw, etc.) are refused.
    return False


def is_allowed_lookup(host: object) -> bool:
    """Return True if ``getaddrinfo(host, ...)`` may run without leaking a hostname."""
    if host is None:
        return True  # Wildcard/passive lookup; no network traffic.
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii")
        except UnicodeDecodeError:
            return False
    if not isinstance(host, str):
        return False
    if host == "" or is_loopback_host(host):
        return True
    # Any IP literal is fine to "resolve": no DNS query happens. connect() still
    # blocks it afterwards if it is not loopback.
    candidate = host.strip("[]").split("%", 1)[0]
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return True


def _blocked(what: str, family: int, address: object) -> NetworkBlockedError:
    logger.warning("netguard blocked %s (family=%s, address=%r)", what, family, address)
    return NetworkBlockedError(
        f"Tanaw network guard: blocked {what} to non-loopback address {address!r}. "
        "Tanaw only talks to this computer."
    )


def _guarded_connect(self: socket.socket, address: Any) -> None:
    if not is_allowed_address(self.family, address):
        raise _blocked("connect", self.family, address)
    _originals["connect"](self, address)


def _guarded_connect_ex(self: socket.socket, address: Any) -> int:
    if not is_allowed_address(self.family, address):
        raise _blocked("connect_ex", self.family, address)
    result: int = _originals["connect_ex"](self, address)
    return result


def _guarded_sendto(self: socket.socket, data: Any, *args: Any) -> int:
    # sendto(data, address) or sendto(data, flags, address)
    address = args[-1] if args else None
    if not is_allowed_address(self.family, address):
        raise _blocked("sendto", self.family, address)
    result: int = _originals["sendto"](self, data, *args)
    return result


def _guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
    if not is_allowed_lookup(host):
        logger.warning("netguard blocked DNS lookup")
        raise NetworkBlockedError(
            "Tanaw network guard: blocked a hostname lookup. Tanaw only talks to this computer."
        )
    return _originals["getaddrinfo"](host, *args, **kwargs)


def install() -> None:
    """Install the guard. Safe to call more than once."""
    with _lock:
        if _originals:
            return
        _originals["connect"] = socket.socket.connect
        _originals["connect_ex"] = socket.socket.connect_ex
        _originals["sendto"] = socket.socket.sendto
        _originals["getaddrinfo"] = socket.getaddrinfo
        # setattr: mypy (rightly) refuses direct assignment to methods.
        setattr(socket.socket, "connect", _guarded_connect)  # noqa: B010
        setattr(socket.socket, "connect_ex", _guarded_connect_ex)  # noqa: B010
        setattr(socket.socket, "sendto", _guarded_sendto)  # noqa: B010
        setattr(socket, "getaddrinfo", _guarded_getaddrinfo)  # noqa: B010
        logger.info("netguard installed (loopback only)")


def uninstall() -> None:
    """Remove the guard and restore the original socket functions (used by tests)."""
    with _lock:
        if not _originals:
            return
        setattr(socket.socket, "connect", _originals["connect"])  # noqa: B010
        setattr(socket.socket, "connect_ex", _originals["connect_ex"])  # noqa: B010
        setattr(socket.socket, "sendto", _originals["sendto"])  # noqa: B010
        setattr(socket, "getaddrinfo", _originals["getaddrinfo"])  # noqa: B010
        _originals.clear()
        logger.info("netguard removed")


def is_installed() -> bool:
    return bool(_originals)


#: A real public address (Cloudflare DNS) used for the self-test. With the guard on,
#: the connect is refused inside Python before any packet is sent.
SELF_TEST_ADDRESS = ("1.1.1.1", 443)


def self_test() -> bool:
    """Deliberately try to reach a public address; True if the guard blocked it.

    Returns False without attempting anything if the guard isn't installed, so
    this never makes a real connection.
    """
    if not is_installed():
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        try:
            probe.connect(SELF_TEST_ADDRESS)
        except NetworkBlockedError:
            return True
        except OSError:
            return False  # reached the real network stack: the guard did NOT block it
    return False  # connected: the guard did NOT block it
