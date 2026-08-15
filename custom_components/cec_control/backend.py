"""Transports that carry CEC messages for the integration.

Two backends implement one interface:

* :class:`LocalBackend` drives a Pulse-Eight style adapter attached to the
  machine running Home Assistant, through libcec's Python bindings.
* :class:`DaemonBackend` talks HTTP to the daemon in this repository's
  ``daemon/`` directory, for an adapter attached to some other machine.

Neither is a "bridge": nothing is translated between protocols. They are two
ways of reaching the same CEC bus, and the entities above them cannot tell
which one they have.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .const import (
    ADDRESS_TV,
    KEY_HOLD_SECONDS,
    OPCODE_STANDBY,
    OPCODE_USER_CONTROL_PRESSED,
    OPCODE_USER_CONTROL_RELEASE,
)

_LOGGER = logging.getLogger(__name__)


class BackendError(Exception):
    """A backend could not carry out a request."""


class BackendUnavailable(BackendError):
    """The CEC adapter is not usable right now.

    Distinct from a malformed request: the caller did nothing wrong, the
    hardware is simply not reachable. Maps to HTTP 503 on the daemon side.
    """


@dataclass(frozen=True, slots=True)
class CecDevice:
    """One device seen on the CEC bus."""

    address: int
    osd_string: str = ""
    vendor: str = ""
    physical_address: str = ""
    cec_version: str = ""
    is_on: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""
        return {
            "address": self.address,
            "osd_string": self.osd_string,
            "vendor": self.vendor,
            "physical_address": self.physical_address,
            "cec_version": self.cec_version,
            "is_on": self.is_on,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CecDevice:
        """Rebuild one device from the daemon's JSON."""
        return cls(
            address=int(data["address"]),
            osd_string=str(data.get("osd_string") or ""),
            vendor=str(data.get("vendor") or ""),
            physical_address=str(data.get("physical_address") or ""),
            cec_version=str(data.get("cec_version") or ""),
            is_on=data.get("is_on"),
        )


@dataclass(frozen=True, slots=True)
class BackendStatus:
    """The result of one bus scan."""

    available: bool
    devices: dict[int, CecDevice] = field(default_factory=dict)
    detail: str = ""

    @property
    def tv(self) -> CecDevice | None:
        """Return the television, if it answered the scan."""
        return self.devices.get(ADDRESS_TV)


class CecBackend(ABC):
    """One way of reaching a CEC bus."""

    @property
    @abstractmethod
    def label(self) -> str:
        """Return a short human-readable description of this transport."""

    @abstractmethod
    async def async_scan(self) -> BackendStatus:
        """Poll the bus and report which devices answered."""

    @abstractmethod
    async def async_transmit(
        self, destination: int, opcode: int, params: bytes = b""
    ) -> None:
        """Send one raw CEC message."""

    @abstractmethod
    async def async_power(self, destination: int, turn_on: bool) -> None:
        """Power a device on, or put it into standby."""

    @abstractmethod
    async def async_set_active_source(self) -> None:
        """Declare this adapter to be the active source."""

    async def async_close(self) -> None:
        """Release anything held open.

        Safe to call more than once. A backend that holds nothing open needs no
        override, which is why this is a concrete no-op rather than abstract.
        """
        return None

    async def async_send_key(self, destination: int, key_code: int) -> None:
        """Press and release one user-control key."""
        await self.async_transmit(
            destination, OPCODE_USER_CONTROL_PRESSED, bytes([key_code])
        )
        await asyncio.sleep(KEY_HOLD_SECONDS)
        await self.async_transmit(destination, OPCODE_USER_CONTROL_RELEASE, b"")


def import_libcec() -> Any:
    """Return the libcec Python bindings, or raise BackendUnavailable.

    The bindings are a compiled extension that ships with the Home Assistant
    image rather than through this integration's requirements, so an install
    that only ever uses the daemon backend must not fail to load because they
    are missing.
    """
    try:
        import cec
    except ImportError as err:  # pragma: no cover - depends on the host image
        raise BackendUnavailable(
            "libcec's Python bindings (the 'cec' module) are not available on "
            "this Home Assistant installation; use the daemon backend instead"
        ) from err
    return cec


class LocalBackend(CecBackend):
    """Drive an adapter attached to this machine through libcec.

    Two properties of the bindings shape this class:

    * ``cec.init()`` opens the adapter for the whole process, and the adapter
      can only be held by one process at a time.
    * A ``cec.Device`` object that is still alive when the interpreter shuts
      down aborts the process (``FATAL: exception not rethrown``, measured on
      libcec 7.0.0 with python-cec 0.2.8 on 2026-08-16). Every device mapping
      obtained here is therefore cleared before returning, and only plain data
      escapes this class.
    """

    def __init__(self, hass: Any, adapter: str | None = None) -> None:
        """Store the adapter to open on first use."""
        self._hass = hass
        self._adapter = adapter
        self._lock = asyncio.Lock()
        self._initialised = False

    @property
    def label(self) -> str:
        """Return the adapter path."""
        return self._adapter or "auto-detected adapter"

    def _init_sync(self) -> None:
        """Open the adapter. Runs in an executor; libcec blocks."""
        cec = import_libcec()
        try:
            if self._adapter:
                cec.init(self._adapter)
            else:
                cec.init()
        except Exception as err:
            raise BackendUnavailable(f"could not open CEC adapter: {err}") from err

    async def _async_ensure_init(self) -> None:
        """Open the adapter once."""
        if self._initialised:
            return
        await self._hass.async_add_executor_job(self._init_sync)
        self._initialised = True

    def _scan_sync(self) -> BackendStatus:
        """Poll the bus, keeping no libcec objects alive afterwards."""
        cec = import_libcec()
        devices = cec.list_devices()
        try:
            found = {
                int(address): CecDevice(
                    address=int(address),
                    osd_string=str(device.osd_string),
                    vendor=str(device.vendor),
                    physical_address=str(device.physical_address),
                    cec_version=str(device.cec_version),
                    is_on=bool(device.is_on()),
                )
                for address, device in devices.items()
            }
        finally:
            # See the class docstring: these must not outlive this call.
            devices.clear()
            del devices
        return BackendStatus(available=True, devices=found)

    async def async_scan(self) -> BackendStatus:
        """Poll the bus."""
        async with self._lock:
            await self._async_ensure_init()
            try:
                return await self._hass.async_add_executor_job(self._scan_sync)
            except BackendError:
                raise
            except Exception as err:
                raise BackendUnavailable(f"CEC bus scan failed: {err}") from err

    def _transmit_sync(self, destination: int, opcode: int, params: bytes) -> None:
        """Send one message through the module-level API.

        Deliberately not ``Device.transmit``: the module-level call never
        creates a ``cec.Device``, so it cannot contribute to the shutdown abort
        described in the class docstring.
        """
        cec = import_libcec()
        if not cec.transmit(destination, opcode, params):
            raise BackendError(
                f"CEC rejected opcode 0x{opcode:02X} to address {destination}"
            )

    async def async_transmit(
        self, destination: int, opcode: int, params: bytes = b""
    ) -> None:
        """Send one raw CEC message."""
        async with self._lock:
            await self._async_ensure_init()
            await self._hass.async_add_executor_job(
                self._transmit_sync, destination, opcode, params
            )

    def _power_sync(self, destination: int, turn_on: bool) -> None:
        """Power on through libcec, or send a plain standby."""
        cec = import_libcec()
        if not turn_on:
            if not cec.transmit(destination, OPCODE_STANDBY, b""):
                raise BackendError(f"standby to address {destination} was rejected")
            return
        # Powering on is more than one message on most televisions, so let
        # libcec drive it. This is the one place a Device object is needed;
        # it is dropped before returning.
        devices = cec.list_devices()
        try:
            device = devices.get(destination)
            if device is None:
                raise BackendUnavailable(
                    f"no device answered at CEC address {destination}"
                )
            if not device.power_on():
                raise BackendError(f"power on to address {destination} was rejected")
        finally:
            devices.clear()
            del devices

    async def async_power(self, destination: int, turn_on: bool) -> None:
        """Power a device on, or put it into standby."""
        async with self._lock:
            await self._async_ensure_init()
            await self._hass.async_add_executor_job(
                self._power_sync, destination, turn_on
            )

    def _active_source_sync(self) -> None:
        """Declare this adapter the active source."""
        cec = import_libcec()
        if not cec.set_active_source():
            raise BackendError("setting the active source was rejected")

    async def async_set_active_source(self) -> None:
        """Declare this adapter to be the active source."""
        async with self._lock:
            await self._async_ensure_init()
            await self._hass.async_add_executor_job(self._active_source_sync)


class DaemonBackend(CecBackend):
    """Reach a CEC adapter attached to another machine over HTTP.

    Speaks the contract in ``docs/daemon-contract.md``. The daemon carries raw
    addresses and opcodes; key names never cross the wire, so the two sides can
    be versioned against each other without a shared vocabulary.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        timeout: float = 10.0,
    ) -> None:
        """Store where the daemon lives."""
        self._session = session
        self._host = host
        self._port = port
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    @property
    def label(self) -> str:
        """Return the daemon's address."""
        return f"{self._host}:{self._port}"

    @property
    def base_url(self) -> str:
        """Return the daemon's base URL."""
        return f"http://{self._host}:{self._port}"

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Call the daemon and translate its status codes."""
        url = f"{self.base_url}{path}"
        try:
            async with self._session.request(
                method, url, json=payload, timeout=self._timeout
            ) as response:
                try:
                    body = await response.json(content_type=None)
                except ValueError:
                    body = {}
                if response.status == 503:
                    raise BackendUnavailable(
                        _daemon_error(body, "the daemon's CEC adapter is unavailable")
                    )
                if response.status >= 400:
                    raise BackendError(
                        _daemon_error(body, f"daemon returned HTTP {response.status}")
                    )
                return body if isinstance(body, dict) else {}
        except TimeoutError as err:
            raise BackendUnavailable(f"{url} timed out") from err
        except aiohttp.ClientError as err:
            raise BackendUnavailable(f"could not reach {url}: {err}") from err

    async def async_health(self) -> dict[str, Any]:
        """Return the daemon's own report of itself."""
        return await self._request("GET", "/health")

    async def async_scan(self) -> BackendStatus:
        """Poll the bus through the daemon."""
        body = await self._request("GET", "/devices")
        devices = {
            int(entry["address"]): CecDevice.from_dict(entry)
            for entry in body.get("devices", [])
        }
        return BackendStatus(available=True, devices=devices)

    async def async_transmit(
        self, destination: int, opcode: int, params: bytes = b""
    ) -> None:
        """Send one raw CEC message through the daemon."""
        await self._request(
            "POST",
            "/transmit",
            {
                "destination": destination,
                "opcode": opcode,
                "params": params.hex(),
            },
        )

    async def async_power(self, destination: int, turn_on: bool) -> None:
        """Power a device on, or put it into standby, through the daemon."""
        await self._request(
            "POST",
            "/power",
            {"destination": destination, "action": "on" if turn_on else "off"},
        )

    async def async_set_active_source(self) -> None:
        """Declare the daemon's adapter to be the active source."""
        await self._request("POST", "/active_source", {})


def _daemon_error(body: Any, fallback: str) -> str:
    """Return the daemon's error sentence, or a fallback."""
    if isinstance(body, dict):
        message = body.get("error")
        if isinstance(message, str) and message:
            return message
    return fallback
