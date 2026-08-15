"""Blocking libcec access, for whichever Python binding is installed.

There are two unrelated Python modules both importable as ``cec``, and which one
you get depends on the machine:

* **libcec's own SWIG binding** (``cec.ICECAdapter``, ``cec.libcec_configuration``).
  This is what the Home Assistant OS image ships — measured as libcec 7.1.1 on
  2026-08-16 — and what the built-in ``hdmi_cec`` integration uses through pyCEC.
  Debian does not package it; it appears when libcec is built with Python
  bindings enabled.
* **the `python-cec` package** (``cec.init``, ``cec.list_devices``,
  ``cec.transmit``). This is what ``pip install cec`` gives you, and the
  practical choice on a Raspberry Pi running the daemon.

They share a name and nothing else. This module hides the difference behind one
small blocking interface so that the integration and the daemon each get
whichever is present without caring.

Everything here is synchronous — libcec blocks — and free of Home Assistant
imports, so the daemon can load it directly from this file.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any

_LOGGER = logging.getLogger(__name__)

OPCODE_STANDBY = 0x36
OPCODE_USER_CONTROL_PRESSED = 0x44
OPCODE_USER_CONTROL_RELEASE = 0x45

# libcec retries opening the adapter, and reports failure even when the adapter
# is in fact usable. Khronos31/lua-remote-hub hit this and settled on: try a few
# times, and treat a successful ping as success regardless of what Open said.
OPEN_ATTEMPTS = 3
OPEN_TIMEOUT_MS = 10_000
OPEN_RETRY_SECONDS = 1.0

# libcec's configuration struct declares strDeviceName as char[13], and SWIG
# rejects anything longer outright rather than truncating. This is the name the
# television shows in its own device list.
DEVICE_NAME = "HA CEC"

# The highest logical address worth polling. 15 is the broadcast address and
# never belongs to a device.
MAX_LOGICAL_ADDRESS = 14


class DriverError(Exception):
    """libcec refused an operation."""


class DriverUnavailable(DriverError):
    """No usable adapter. The caller did nothing wrong."""


def import_cec() -> Any:
    """Return whichever ``cec`` module is installed."""
    try:
        import cec
    except ImportError as err:
        raise DriverUnavailable(
            "no libcec Python binding is available; install libcec with Python "
            "bindings, or 'pip install cec'"
        ) from err
    return cec


def flavour(cec: Any) -> str:
    """Return which of the two ``cec`` modules this is."""
    if hasattr(cec, "ICECAdapter"):
        return "swig"
    if hasattr(cec, "init"):
        return "python-cec"
    raise DriverUnavailable(
        "the installed 'cec' module is neither libcec's SWIG binding nor "
        "python-cec; refusing to guess at its API"
    )


def format_physical_address(value: int) -> str:
    """Render a physical address the way CEC documentation writes it."""
    return ".".join(str((int(value) >> shift) & 0xF) for shift in (12, 8, 4, 0))


class LibcecDriver(ABC):
    """One open CEC adapter."""

    @abstractmethod
    def open(self) -> None:
        """Open the adapter. Raises DriverUnavailable if it cannot."""

    @abstractmethod
    def close(self) -> None:
        """Release the adapter. Safe to call when not open."""

    @abstractmethod
    def scan(self) -> list[dict[str, Any]]:
        """Return one entry per logical address that answered."""

    @abstractmethod
    def transmit(self, destination: int, opcode: int, params: bytes) -> bool:
        """Send one raw CEC message."""

    @abstractmethod
    def power(self, destination: int, turn_on: bool) -> bool:
        """Power one device on, or put it into standby."""

    @abstractmethod
    def set_active_source(self) -> bool:
        """Declare this adapter to be the active source."""

    @abstractmethod
    def set_stream_path(self, physical_address: int) -> bool:
        """Ask the television to show the given physical address."""

    def send_key(self, destination: int, key_code: int, hold: float = 0.2) -> bool:
        """Press and release one user-control key.

        The default builds the two raw messages by hand; a binding that offers
        key sending natively overrides this.
        """
        pressed = self.transmit(
            destination, OPCODE_USER_CONTROL_PRESSED, bytes([key_code])
        )
        time.sleep(hold)
        released = self.transmit(destination, OPCODE_USER_CONTROL_RELEASE, b"")
        return pressed and released


class SwigDriver(LibcecDriver):
    """libcec's own SWIG binding."""

    def __init__(self, cec: Any, adapter: str | None = None) -> None:
        """Remember which adapter to open."""
        self._cec = cec
        self._adapter = adapter
        self._lib: Any = None

    def _configure(self) -> Any:
        """Return a libcec instance that has not opened a port yet."""
        cec = self._cec
        config = cec.libcec_configuration()
        config.Clear()
        config.strDeviceName = DEVICE_NAME
        config.clientVersion = cec.LIBCEC_VERSION_CURRENT
        # Do not steal the picture just by connecting.
        config.bActivateSource = 0
        config.deviceTypes.Add(cec.CEC_DEVICE_TYPE_RECORDING_DEVICE)
        lib = cec.ICECAdapter.Create(config)
        if lib is None:
            raise DriverUnavailable("libcec would not initialise")
        lib.InitVideoStandalone()
        return lib

    def detect_adapters(self) -> list[str]:
        """Return the adapters libcec can see, opening nothing."""
        lib = self._configure()
        try:
            return [str(found.strComName) for found in lib.DetectAdapters()]
        finally:
            lib.Close()

    def open(self) -> None:
        """Open the adapter, tolerating libcec's unreliable return value."""
        if self._lib is not None:
            return
        lib = self._configure()
        port = self._adapter
        if not port:
            detected = lib.DetectAdapters()
            if not len(detected):
                lib.Close()
                raise DriverUnavailable("no CEC adapter found")
            port = str(detected[0].strComName)

        for attempt in range(OPEN_ATTEMPTS):
            # Open() reports failure on adapters that are in fact working, so a
            # successful ping overrides it.
            if lib.Open(port, OPEN_TIMEOUT_MS) or lib.PingAdapter():
                self._lib = lib
                return
            if attempt < OPEN_ATTEMPTS - 1:
                time.sleep(OPEN_RETRY_SECONDS)
        lib.Close()
        raise DriverUnavailable(f"could not open CEC adapter {port!r}")

    def close(self) -> None:
        """Release the adapter."""
        if self._lib is None:
            return
        lib, self._lib = self._lib, None
        lib.Close()

    def _require(self) -> Any:
        if self._lib is None:
            raise DriverUnavailable("the CEC adapter is not open")
        return self._lib

    def scan(self) -> list[dict[str, Any]]:
        """Poll each logical address for the devices that answer."""
        lib = self._require()
        lib.RescanActiveDevices()
        power_on = self._cec.CEC_POWER_STATUS_ON
        devices = []
        for address in range(MAX_LOGICAL_ADDRESS + 1):
            if not lib.IsActiveDevice(address):
                continue
            devices.append(
                {
                    "address": address,
                    "osd_string": str(lib.GetDeviceOSDName(address)),
                    "vendor": f"{int(lib.GetDeviceVendorId(address)):06X}",
                    "physical_address": format_physical_address(
                        lib.GetDevicePhysicalAddress(address)
                    ),
                    "cec_version": str(
                        lib.CecVersionToString(lib.GetDeviceCecVersion(address))
                    ),
                    "is_on": lib.GetDevicePowerStatus(address) == power_on,
                }
            )
        return devices

    def transmit(self, destination: int, opcode: int, params: bytes) -> bool:
        """Send one raw message, built the way cec-client writes them."""
        lib = self._require()
        initiator = lib.GetLogicalAddresses().primary
        frame = f"{initiator:X}{destination:X}:{opcode:02X}"
        if params:
            frame += ":" + ":".join(f"{byte:02X}" for byte in params)
        return bool(lib.Transmit(lib.CommandFromString(frame)))

    def send_key(self, destination: int, key_code: int, hold: float = 0.2) -> bool:
        """Use libcec's own key handling, which manages the hold itself."""
        lib = self._require()
        pressed = bool(lib.SendKeypress(destination, key_code, True))
        released = bool(lib.SendKeyRelease(destination, True))
        return pressed and released

    def power(self, destination: int, turn_on: bool) -> bool:
        """Power on or stand by through libcec's own helpers."""
        lib = self._require()
        if turn_on:
            return bool(lib.PowerOnDevices(destination))
        return bool(lib.StandbyDevices(destination))

    def set_active_source(self) -> bool:
        """Declare this adapter to be the active source."""
        return bool(self._require().SetActiveSource())

    def set_stream_path(self, physical_address: int) -> bool:
        """Ask the television to show one physical address."""
        return bool(self._require().SetStreamPath(physical_address))


class PythonCecDriver(LibcecDriver):
    """The `python-cec` package.

    A ``cec.Device`` that is still alive at interpreter shutdown aborts the
    process (measured on python-cec 0.2.8 / libcec 7.0.0). Every mapping this
    class obtains is cleared before it can escape, and sends go through the
    module-level API, which never creates one.
    """

    def __init__(self, cec: Any, adapter: str | None = None) -> None:
        """Remember which adapter to open."""
        self._cec = cec
        self._adapter = adapter
        self._opened = False

    def detect_adapters(self) -> list[str]:
        """Return the adapters libcec can see."""
        return [str(path) for path in self._cec.list_adapters()]

    def open(self) -> None:
        """Open the adapter for this process."""
        if self._opened:
            return
        try:
            if self._adapter:
                self._cec.init(self._adapter)
            else:
                self._cec.init()
        except Exception as err:
            raise DriverUnavailable(f"could not open CEC adapter: {err}") from err
        self._opened = True

    def close(self) -> None:
        """python-cec offers no way to release the adapter short of exiting."""
        self._opened = False

    def scan(self) -> list[dict[str, Any]]:
        """Poll the bus, keeping no Device objects afterwards."""
        devices = self._cec.list_devices()
        try:
            return [
                {
                    "address": int(address),
                    "osd_string": str(device.osd_string),
                    "vendor": str(device.vendor),
                    "physical_address": str(device.physical_address),
                    "cec_version": str(device.cec_version),
                    "is_on": bool(device.is_on()),
                }
                for address, device in sorted(devices.items())
            ]
        finally:
            devices.clear()
            del devices

    def transmit(self, destination: int, opcode: int, params: bytes) -> bool:
        """Send one raw message without creating a Device."""
        return bool(self._cec.transmit(destination, opcode, params))

    def power(self, destination: int, turn_on: bool) -> bool:
        """Power on through libcec; stand by with a plain opcode."""
        if not turn_on:
            return bool(self._cec.transmit(destination, OPCODE_STANDBY, b""))
        devices = self._cec.list_devices()
        try:
            device = devices.get(destination)
            return False if device is None else bool(device.power_on())
        finally:
            devices.clear()
            del devices

    def set_active_source(self) -> bool:
        """Declare this adapter to be the active source."""
        return bool(self._cec.set_active_source())

    def set_stream_path(self, physical_address: int) -> bool:
        """Ask the television to show one physical address."""
        self._cec.set_stream_path(physical_address)
        return True


def create_driver(adapter: str | None = None, cec: Any = None) -> LibcecDriver:
    """Return a driver for whichever binding is installed."""
    module = cec if cec is not None else import_cec()
    kind = flavour(module)
    _LOGGER.debug("using the %s libcec binding", kind)
    if kind == "swig":
        return SwigDriver(module, adapter)
    return PythonCecDriver(module, adapter)


def detect_adapters(cec: Any = None) -> list[str]:
    """Return the CEC adapters attached to this machine."""
    driver = create_driver(None, cec)
    return driver.detect_adapters()  # type: ignore[attr-defined]
