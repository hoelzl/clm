"""Tests for the Worker API's port policy and socket binding.

Companion to ``test_worker_api_auth.py``, which covers the *address* half of
:mod:`clm.infrastructure.api.binding`. This file covers the *port* half:

- who decides the port                    → :class:`TestResolvePort`
- a second listener cannot silently share → :class:`TestBindSocket`
- an advisory port yields, a pinned one   → :class:`TestBindSocketWithFallback`
  does not

The middle one is the regression test for the confusing failure this policy
exists to remove. ``SO_REUSEADDR`` on Windows lets a second socket bind an
address a first one is still listening on, after which the OS decides which
socket gets a given connection. For a service that hands out jobs, that means a
container registering with a server that owns a different jobs database — the
symptom being a job that stays ``pending`` with no error anywhere.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest

from clm.infrastructure.api.binding import (
    DEFAULT_PORT,
    EPHEMERAL_PORT,
    LOOPBACK_HOST,
    PORT_ENV_VAR,
    bind_socket,
    bind_socket_with_fallback,
    is_address_in_use,
    resolve_port,
)

#: An address in TEST-NET-1 (RFC 5737), guaranteed not to be configured on this
#: host — so binding it fails for a reason that is *not* "port in use".
UNCONFIGURED_HOST = "192.0.2.1"


@pytest.fixture
def occupied_port() -> Iterator[int]:
    """Bind a loopback port for the duration of a test and yield its number.

    Port 0 keeps this collision-proof under pytest-xdist: the OS hands out a
    port nothing else holds, and the socket stays open for the whole test, so
    "already in use" is a fact rather than a hope.
    """
    sock = bind_socket(LOOPBACK_HOST, EPHEMERAL_PORT)
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()


class TestResolvePort:
    """The port comes from the caller, then the environment, then the default."""

    def test_default_when_nothing_asks(self) -> None:
        # Unpinned: the default is a convenience, so a collision may be routed
        # around rather than raised.
        assert resolve_port(None) == (DEFAULT_PORT, False)

    def test_explicit_port_is_pinned(self) -> None:
        assert resolve_port(9101) == (9101, True)

    def test_env_var_is_pinned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PORT_ENV_VAR, "9102")
        assert resolve_port(None) == (9102, True)

    def test_env_var_zero_requests_an_os_assigned_port(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """This is what the test suite sets, so every server gets its own port."""
        monkeypatch.setenv(PORT_ENV_VAR, "0")
        assert resolve_port(None) == (EPHEMERAL_PORT, True)

    def test_explicit_port_wins_over_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PORT_ENV_VAR, "9102")
        assert resolve_port(9103) == (9103, True)

    def test_blank_env_var_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PORT_ENV_VAR, "   ")
        assert resolve_port(None) == (DEFAULT_PORT, False)

    @pytest.mark.parametrize("value", ["not-a-port", "8765.5", "-1", "65536"])
    def test_unusable_env_var_is_an_error(
        self, value: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Never silently serve somewhere other than where the operator asked.

        A typo'd port that fell back to the default would produce a server the
        operator's firewall rule, ``curl``, or second machine cannot see, with
        nothing pointing at the typo.
        """
        monkeypatch.setenv(PORT_ENV_VAR, value)
        with pytest.raises(ValueError, match=PORT_ENV_VAR):
            resolve_port(None)


class TestBindSocket:
    def test_ephemeral_port_reports_the_real_one(self) -> None:
        sock = bind_socket(LOOPBACK_HOST, EPHEMERAL_PORT)
        try:
            assert sock.getsockname()[1] != 0
        finally:
            sock.close()

    def test_second_listener_on_the_same_port_is_refused(self, occupied_port: int) -> None:
        """The no-silent-sharing guarantee, on every platform.

        POSIX refuses this regardless; Windows only refuses it because
        ``bind_socket`` withholds ``SO_REUSEADDR`` there. Without that, the
        second bind succeeds and which server receives a container's callback
        becomes undefined — the whole reason this test exists.
        """
        with pytest.raises(OSError) as excinfo:
            bind_socket(LOOPBACK_HOST, occupied_port)
        assert is_address_in_use(excinfo.value)

    def test_port_is_reusable_once_released(self) -> None:
        """Sequential use is still fine — it is *concurrent* use that is not."""
        first = bind_socket(LOOPBACK_HOST, EPHEMERAL_PORT)
        port = first.getsockname()[1]
        first.close()

        second = bind_socket(LOOPBACK_HOST, port)
        second.close()


class TestBindSocketWithFallback:
    def test_free_port_is_taken_as_asked(self) -> None:
        sock = bind_socket_with_fallback(LOOPBACK_HOST, EPHEMERAL_PORT, allow_fallback=False)
        try:
            assert sock.getsockname()[1] != 0
        finally:
            sock.close()

    def test_taken_port_moves_when_fallback_is_allowed(
        self, occupied_port: int, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An advisory default port yields to whoever already has it.

        Two concurrent builds on one machine is a normal thing to do; the
        second one does not need the default port, because it tells its own
        containers where to call back.
        """
        with caplog.at_level("WARNING"):
            sock = bind_socket_with_fallback(LOOPBACK_HOST, occupied_port, allow_fallback=True)
        try:
            assert sock.getsockname()[1] != occupied_port
        finally:
            sock.close()

        assert any(str(occupied_port) in rec.message for rec in caplog.records), (
            "Moving to a different port must be logged — otherwise the operator "
            "looks for the server on the port they expected"
        )

    def test_taken_port_raises_when_pinned(self, occupied_port: int) -> None:
        with pytest.raises(OSError) as excinfo:
            bind_socket_with_fallback(LOOPBACK_HOST, occupied_port, allow_fallback=False)
        assert is_address_in_use(excinfo.value)

    def test_other_bind_failures_are_not_papered_over(self) -> None:
        """Fallback is for "port taken", not for "that address is not here".

        An unbindable *address* stays an error even with fallback allowed:
        another port on an address this host does not have is just as
        unreachable, and the second failure would hide the first.
        """
        with pytest.raises(OSError) as excinfo:
            bind_socket_with_fallback(UNCONFIGURED_HOST, EPHEMERAL_PORT, allow_fallback=True)
        assert not is_address_in_use(excinfo.value)


class TestIsAddressInUse:
    def test_recognises_the_platform_errno(self) -> None:
        """Windows reports WSAEADDRINUSE (10048), POSIX EADDRINUSE (98)."""
        sock = bind_socket(LOOPBACK_HOST, EPHEMERAL_PORT)
        try:
            with pytest.raises(OSError) as excinfo:
                bind_socket(LOOPBACK_HOST, sock.getsockname()[1])
        finally:
            sock.close()
        assert is_address_in_use(excinfo.value)

    def test_other_errors_are_not_in_use(self) -> None:
        assert not is_address_in_use(OSError(socket.EAI_FAIL, "resolution failure"))
