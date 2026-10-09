from __future__ import annotations

import socket
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from tanaw import netguard
from tanaw.netguard import NetworkBlockedError

# 203.0.113.0/24 is TEST-NET-3 (RFC 5737): never routed. The guard refuses before
# any packet is sent, so these tests never touch the network either way.
NON_LOOPBACK = "203.0.113.7"


@pytest.fixture
def guard() -> Iterator[None]:
    netguard.install()
    try:
        yield
    finally:
        netguard.uninstall()


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1", "127.5.6.7", "::1", "[::1]", "localhost", "LOCALHOST",
        "::ffff:127.0.0.1", b"127.0.0.1",
    ],
)
def test_loopback_hosts_are_allowed(host: object) -> None:
    assert netguard.is_loopback_host(host)


@pytest.mark.parametrize(
    "host",
    [
        NON_LOOPBACK, "8.8.8.8", "0.0.0.0", "192.168.1.10", "::ffff:8.8.8.8",  # noqa: S104
        "fe80::1%3", "example.com", "localhost.evil.com", "", None, 12345, b"\xff",
    ],
)
def test_non_loopback_hosts_are_refused(host: object) -> None:
    assert not netguard.is_loopback_host(host)


def test_address_family_rules() -> None:
    assert netguard.is_allowed_address(socket.AF_INET, ("127.0.0.1", 11434))
    assert netguard.is_allowed_address(socket.AF_INET6, ("::1", 11434, 0, 0))
    assert not netguard.is_allowed_address(socket.AF_INET, (NON_LOOPBACK, 80))
    assert not netguard.is_allowed_address(socket.AF_INET, "127.0.0.1")  # malformed
    assert not netguard.is_allowed_address(socket.AF_INET, None)
    assert not netguard.is_allowed_address(-1, ("127.0.0.1", 80))  # unknown family


def test_lookup_rules() -> None:
    assert netguard.is_allowed_lookup(None)
    assert netguard.is_allowed_lookup("localhost")
    assert netguard.is_allowed_lookup("127.0.0.1")
    assert netguard.is_allowed_lookup(NON_LOOPBACK)  # IP literal: no DNS query
    assert not netguard.is_allowed_lookup("example.com")
    assert not netguard.is_allowed_lookup(b"example.com")


def test_install_is_idempotent_and_reversible() -> None:
    original = socket.socket.connect
    netguard.install()
    netguard.install()
    assert netguard.is_installed()
    assert socket.socket.connect is not original
    netguard.uninstall()
    assert not netguard.is_installed()
    assert socket.socket.connect is original


@pytest.mark.usefixtures("guard")
def test_tcp_connect_to_internet_is_blocked() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s, pytest.raises(NetworkBlockedError):
        s.connect((NON_LOOPBACK, 80))


@pytest.mark.usefixtures("guard")
def test_connect_ex_is_blocked() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s, pytest.raises(NetworkBlockedError):
        s.connect_ex((NON_LOOPBACK, 80))


@pytest.mark.usefixtures("guard")
def test_udp_sendto_is_blocked() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s, pytest.raises(NetworkBlockedError):
        s.sendto(b"x", (NON_LOOPBACK, 53))


@pytest.mark.usefixtures("guard")
def test_dns_lookup_is_blocked() -> None:
    with pytest.raises(NetworkBlockedError):
        socket.getaddrinfo("example.com", 443)


@pytest.mark.usefixtures("guard")
def test_http_library_is_blocked() -> None:
    with pytest.raises((NetworkBlockedError, urllib.error.URLError)):
        urllib.request.urlopen("http://example.com", timeout=2)


@pytest.mark.usefixtures("guard")
def test_loopback_connection_still_works() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            conn, _ = server.accept()
            with conn:
                client.sendall(b"ping")
                assert conn.recv(4) == b"ping"
