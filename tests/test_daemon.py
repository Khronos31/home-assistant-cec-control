"""Tests for the daemon's HTTP contract.

These drive the real application with a stand-in adapter, so they check what a
caller actually receives — status code and body shape — rather than what the
handlers intended to return. The contract is documented in
`docs/daemon-contract.md`; if a test here disagrees with that file, one of them
is wrong and it matters which.
"""

from __future__ import annotations

from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from daemon.cec_daemon import AdapterUnavailable, CecRejected, build_app


class FakeAdapter:
    """An adapter that records calls instead of touching hardware."""

    def __init__(
        self,
        *,
        available: bool = True,
        rejects: bool = False,
        adapter: str | None = None,
    ) -> None:
        """Configure how this stand-in behaves."""
        self.adapter = adapter
        self._available = available
        self._rejects = rejects
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _guard(self) -> None:
        if not self._available:
            raise AdapterUnavailable("no adapter here")

    async def health(self) -> dict[str, Any]:
        """Report usability without failing."""
        if not self._available:
            return {"device_ok": False, "detail": "no adapter here"}
        return {"device_ok": True, "detail": ""}

    async def scan(self) -> list[dict[str, Any]]:
        """Return one fixed device."""
        self._guard()
        self.calls.append(("scan", ()))
        return [
            {
                "address": 0,
                "osd_string": "TV",
                "vendor": "000000",
                "physical_address": "0.0.0.0",
                "cec_version": "1.4",
                "is_on": True,
            }
        ]

    async def transmit(self, destination: int, opcode: int, params: bytes) -> None:
        """Record one raw send."""
        self._guard()
        if self._rejects:
            raise CecRejected("bus did not acknowledge")
        self.calls.append(("transmit", (destination, opcode, params)))

    async def power(self, destination: int, turn_on: bool) -> None:
        """Record one power command."""
        self._guard()
        if self._rejects:
            raise CecRejected("bus did not acknowledge")
        self.calls.append(("power", (destination, turn_on)))

    async def set_active_source(self) -> None:
        """Record one active-source command."""
        self._guard()
        self.calls.append(("set_active_source", ()))


async def make_client(adapter: FakeAdapter) -> TestClient:
    """Return a client wired to one stand-in adapter."""
    client = TestClient(TestServer(build_app(adapter)))
    await client.start_server()
    return client


@pytest.fixture
async def adapter() -> FakeAdapter:
    """Return a healthy stand-in adapter."""
    return FakeAdapter(adapter="/dev/ttyACM0")


@pytest.fixture
async def client(adapter: FakeAdapter):
    """Return a client for a healthy daemon."""
    started = await make_client(adapter)
    yield started
    await started.close()


async def test_health_reports_the_service_and_version(client: TestClient) -> None:
    """The integration identifies a daemon, and its version, from /health."""
    response = await client.get("/health")
    assert response.status == 200
    body = await response.json()
    assert body["service"] == "cec-control-daemon"
    assert body["device_ok"] is True
    assert body["adapter"] == "/dev/ttyACM0"
    assert body["version"]


async def test_health_is_200_even_without_an_adapter() -> None:
    """"Daemon down" and "adapter unplugged" must stay distinguishable."""
    broken = FakeAdapter(available=False)
    started = await make_client(broken)
    try:
        response = await started.get("/health")
        assert response.status == 200
        body = await response.json()
        assert body["device_ok"] is False
        assert body["detail"]
    finally:
        await started.close()


async def test_devices_returns_the_scan(client: TestClient) -> None:
    """A bus scan comes back as plain JSON."""
    response = await client.get("/devices")
    assert response.status == 200
    body = await response.json()
    assert body["devices"][0]["address"] == 0
    assert body["devices"][0]["is_on"] is True


async def test_transmit_passes_hex_params_through(
    client: TestClient, adapter: FakeAdapter
) -> None:
    """Params travel as a hex string and arrive as bytes."""
    response = await client.post(
        "/transmit", json={"destination": 0, "opcode": 0x44, "params": "35"}
    )
    assert response.status == 200
    assert ("transmit", (0, 0x44, b"\x35")) in adapter.calls


async def test_transmit_accepts_colon_separated_params(
    client: TestClient, adapter: FakeAdapter
) -> None:
    """CEC is written with colons everywhere else, so accept them here too."""
    response = await client.post(
        "/transmit", json={"destination": 15, "opcode": 0x82, "params": "10:00"}
    )
    assert response.status == 200
    assert ("transmit", (15, 0x82, b"\x10\x00")) in adapter.calls


async def test_power_records_the_direction(
    client: TestClient, adapter: FakeAdapter
) -> None:
    """on and off reach the adapter as a boolean."""
    on = await client.post("/power", json={"destination": 0, "action": "on"})
    off = await client.post("/power", json={"destination": 0, "action": "off"})
    assert on.status == 200
    assert off.status == 200
    assert ("power", (0, True)) in adapter.calls
    assert ("power", (0, False)) in adapter.calls


@pytest.mark.parametrize(
    "payload",
    [
        {"opcode": 0x44},
        {"destination": 0},
        {"destination": 99, "opcode": 0x44},
        {"destination": 0, "opcode": 999},
        {"destination": 0, "opcode": 0x44, "params": "zz"},
        {"destination": True, "opcode": 0x44},
    ],
)
async def test_malformed_transmit_is_400(client: TestClient, payload: dict) -> None:
    """A caller error is 400 with an explanation, never a 500."""
    response = await client.post("/transmit", json=payload)
    assert response.status == 400
    assert (await response.json())["error"]


async def test_invalid_json_is_400(client: TestClient) -> None:
    """A body that is not JSON is the caller's problem."""
    response = await client.post(
        "/transmit", data="not json", headers={"Content-Type": "application/json"}
    )
    assert response.status == 400
    assert (await response.json())["error"]


async def test_bad_action_is_400(client: TestClient) -> None:
    """Only on and off mean anything."""
    response = await client.post("/power", json={"destination": 0, "action": "zap"})
    assert response.status == 400
    assert "on" in (await response.json())["error"]


async def test_unknown_path_is_404_in_the_error_shape(client: TestClient) -> None:
    """Even 404 uses the contract's error body, so one parser handles all."""
    response = await client.get("/nope")
    assert response.status == 404
    assert (await response.json())["error"]


async def test_missing_adapter_is_503() -> None:
    """The request was fine; the hardware is not there. That is 503."""
    broken = FakeAdapter(available=False)
    started = await make_client(broken)
    try:
        response = await started.post(
            "/transmit", json={"destination": 0, "opcode": 0x44}
        )
        assert response.status == 503
        assert (await response.json())["error"]
    finally:
        await started.close()


async def test_unacknowledged_bus_is_503() -> None:
    """A well-formed message the bus refused is also the far side failing."""
    stubborn = FakeAdapter(rejects=True)
    started = await make_client(stubborn)
    try:
        response = await started.post(
            "/transmit", json={"destination": 0, "opcode": 0x44}
        )
        assert response.status == 503
    finally:
        await started.close()
