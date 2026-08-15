"""Tests for the transports.

The daemon tests check what the daemon returns; these check that the
integration's client agrees with it. They are wired to each other directly —
the real `DaemonBackend` talking to the real daemon application — because the
expensive failure is not either side being wrong on its own, it is the two
drifting apart while both look correct in isolation.
"""

from __future__ import annotations

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from custom_components.cec_control.backend import (
    BackendError,
    BackendUnavailable,
    CecDevice,
    DaemonBackend,
)
from daemon.cec_daemon import build_app

from .test_daemon import FakeAdapter


class _Wired:
    """One DaemonBackend pointed at one live daemon."""

    def __init__(self, adapter: FakeAdapter) -> None:
        self.adapter = adapter
        self.server = TestServer(build_app(adapter))
        self.session: aiohttp.ClientSession | None = None
        self.backend: DaemonBackend | None = None

    async def __aenter__(self) -> DaemonBackend:
        await self.server.start_server()
        self.session = aiohttp.ClientSession()
        self.backend = DaemonBackend(
            self.session, self.server.host, self.server.port, timeout=5.0
        )
        return self.backend

    async def __aexit__(self, *exc: object) -> None:
        if self.session is not None:
            await self.session.close()
        await self.server.close()


async def test_health_round_trips() -> None:
    """The client reads the daemon's own report of itself."""
    async with _Wired(FakeAdapter(adapter="/dev/ttyACM0")) as backend:
        health = await backend.async_health()
    assert health["service"] == "cec-control-daemon"
    assert health["device_ok"] is True


async def test_scan_round_trips_into_devices() -> None:
    """A daemon scan arrives as CecDevice objects, not raw dicts."""
    async with _Wired(FakeAdapter()) as backend:
        status = await backend.async_scan()
    assert status.available is True
    assert isinstance(status.tv, CecDevice)
    assert status.tv.address == 0
    assert status.tv.is_on is True
    assert status.tv.osd_string == "TV"


async def test_transmit_reaches_the_adapter_with_the_same_bytes() -> None:
    """Params survive the hex encoding in both directions."""
    adapter = FakeAdapter()
    async with _Wired(adapter) as backend:
        await backend.async_transmit(0, 0x44, b"\x35")
    assert ("transmit", (0, 0x44, b"\x35")) in adapter.calls


async def test_send_key_presses_and_releases() -> None:
    """One key is two CEC messages, and the client sends both."""
    adapter = FakeAdapter()
    async with _Wired(adapter) as backend:
        await backend.async_send_key(0, 0x53)
    opcodes = [call[1][1] for call in adapter.calls if call[0] == "transmit"]
    assert opcodes == [0x44, 0x45]


async def test_power_round_trips() -> None:
    """on and off survive the trip."""
    adapter = FakeAdapter()
    async with _Wired(adapter) as backend:
        await backend.async_power(0, True)
        await backend.async_power(0, False)
    assert ("power", (0, True)) in adapter.calls
    assert ("power", (0, False)) in adapter.calls


async def test_missing_adapter_becomes_backend_unavailable() -> None:
    """A 503 from the daemon is not the same kind of failure as a 400."""
    async with _Wired(FakeAdapter(available=False)) as backend:
        with pytest.raises(BackendUnavailable):
            await backend.async_transmit(0, 0x44, b"")


async def test_daemon_error_message_reaches_the_caller() -> None:
    """The daemon's sentence is what the user sees, not a generic failure."""
    async with _Wired(FakeAdapter(available=False)) as backend:
        with pytest.raises(BackendUnavailable, match="no adapter here"):
            await backend.async_scan()


async def test_unreachable_daemon_is_unavailable_not_an_error() -> None:
    """A daemon that is switched off should retry, not raise a hard failure."""
    async with aiohttp.ClientSession() as session:
        # Port 1 is reserved and nothing listens there.
        backend = DaemonBackend(session, "127.0.0.1", 1, timeout=2.0)
        with pytest.raises(BackendUnavailable):
            await backend.async_health()


async def test_bad_request_is_a_plain_backend_error() -> None:
    """A 400 means the integration sent something wrong; that is a bug, not a
    retry."""
    async with _Wired(FakeAdapter()) as backend:
        with pytest.raises(BackendError) as caught:
            await backend.async_transmit(99, 0x44, b"")
    assert not isinstance(caught.value, BackendUnavailable)


def test_cec_device_survives_a_json_round_trip() -> None:
    """as_dict and from_dict are inverses; the wire format depends on it."""
    original = CecDevice(
        address=4,
        osd_string="Streamer",
        vendor="001A11",
        physical_address="1.1.0.0",
        cec_version="2.0",
        is_on=False,
    )
    assert CecDevice.from_dict(original.as_dict()) == original
