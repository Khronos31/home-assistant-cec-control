"""Tests for the libcec driver.

Two unrelated modules are importable as ``cec``, and only one of them exists on
any given machine — so neither can be tested against the real thing everywhere.
These use stand-ins that mimic each binding's shape, which is enough to catch
the failure that actually happens: reaching for a method the *other* binding
has.
"""

from __future__ import annotations

import pytest

from custom_components.cec_control.libcec_driver import (
    DriverUnavailable,
    PythonCecDriver,
    SwigDriver,
    create_driver,
    flavour,
    format_physical_address,
)


class FakeSwigConfig:
    """Stands in for cec.libcec_configuration."""

    def __init__(self) -> None:
        self.strDeviceName = ""
        self.deviceTypes = self
        self.added: list[int] = []

    def Clear(self) -> None:
        """Reset, as libcec requires before use."""

    def Add(self, device_type: int) -> None:
        """Record a declared device type."""
        self.added.append(device_type)


class FakeAdapterDescriptor:
    """Stands in for cec.AdapterDescriptor."""

    def __init__(self, name: str) -> None:
        self.strComName = name


class FakeLib:
    """Stands in for the object cec.ICECAdapter.Create returns."""

    def __init__(self, *, open_ok: bool = True, ping_ok: bool = True) -> None:
        self._open_ok = open_ok
        self._ping_ok = ping_ok
        self.calls: list[tuple] = []
        self.closed = False

    def InitVideoStandalone(self) -> None:
        """No-op."""

    def DetectAdapters(self) -> list[FakeAdapterDescriptor]:
        """Report one adapter."""
        return [FakeAdapterDescriptor("/dev/ttyACM0")]

    def Open(self, port: str, timeout: int) -> bool:
        """Record the open attempt."""
        self.calls.append(("Open", port, timeout))
        return self._open_ok

    def PingAdapter(self) -> bool:
        """Report reachability."""
        return self._ping_ok

    def Close(self) -> None:
        """Record the close."""
        self.closed = True

    def GetLogicalAddresses(self):
        """Report our own logical address."""
        return type("Addresses", (), {"primary": 1})()

    def CommandFromString(self, frame: str):
        """Return the frame itself; Transmit records it."""
        return frame

    def Transmit(self, command) -> bool:
        """Record a raw send."""
        self.calls.append(("Transmit", command))
        return True

    def SendKeypress(self, destination: int, key: int, wait: bool) -> bool:
        """Record a native keypress."""
        self.calls.append(("SendKeypress", destination, key))
        return True

    def SendKeyRelease(self, destination: int, wait: bool) -> bool:
        """Record a native key release."""
        self.calls.append(("SendKeyRelease", destination))
        return True

    def PowerOnDevices(self, destination: int) -> bool:
        """Record a power on."""
        self.calls.append(("PowerOnDevices", destination))
        return True

    def StandbyDevices(self, destination: int) -> bool:
        """Record a standby."""
        self.calls.append(("StandbyDevices", destination))
        return True

    def SetActiveSource(self) -> bool:
        """Record an active-source claim."""
        self.calls.append(("SetActiveSource",))
        return True

    def SetStreamPath(self, physical: int) -> bool:
        """Record a stream-path request."""
        self.calls.append(("SetStreamPath", physical))
        return True

    def RescanActiveDevices(self) -> None:
        """No-op."""

    def IsActiveDevice(self, address: int) -> bool:
        """Only the television answers."""
        return address == 0

    def GetDeviceOSDName(self, address: int) -> str:
        """Report a name."""
        return "TV"

    def GetDeviceVendorId(self, address: int) -> int:
        """Report a vendor id."""
        return 0x001582

    def GetDevicePhysicalAddress(self, address: int) -> int:
        """Report a physical address."""
        return 0x1000

    def GetDeviceCecVersion(self, address: int) -> int:
        """Report a CEC version code."""
        return 5

    def CecVersionToString(self, code: int) -> str:
        """Render a CEC version."""
        return "1.4"

    def GetDevicePowerStatus(self, address: int) -> int:
        """Report the device as on."""
        return 0


class FakeSwigModule:
    """Stands in for libcec's SWIG binding."""

    LIBCEC_VERSION_CURRENT = 7
    CEC_DEVICE_TYPE_RECORDING_DEVICE = 1
    CEC_POWER_STATUS_ON = 0

    def __init__(self, lib: FakeLib | None = None) -> None:
        self.lib = lib or FakeLib()
        module = self

        class ICECAdapter:
            @staticmethod
            def Create(config):
                module.config = config
                return module.lib

        self.ICECAdapter = ICECAdapter

    def libcec_configuration(self) -> FakeSwigConfig:
        """Return a fresh configuration."""
        return FakeSwigConfig()


class FakeDevice:
    """Stands in for python-cec's Device."""

    def __init__(self) -> None:
        self.osd_string = "TV"
        self.vendor = "000000"
        self.physical_address = "0.0.0.0"
        self.cec_version = "1.4"

    def is_on(self) -> bool:
        """Report the device as on."""
        return True

    def power_on(self) -> bool:
        """Report success."""
        return True


class TrackingDict(dict):
    """A device mapping that remembers whether it was cleared."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cleared = False

    def clear(self) -> None:
        """Record the clear the driver is required to perform."""
        self.cleared = True
        super().clear()


class FakePythonCecModule:
    """Stands in for the python-cec package."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.mappings: list[TrackingDict] = []

    def init(self, adapter: str | None = None) -> None:
        """Record the open."""
        self.calls.append(("init", adapter))

    def list_adapters(self) -> list[str]:
        """Report one adapter."""
        return ["/dev/ttyACM0"]

    def list_devices(self) -> TrackingDict:
        """Return a fresh tracked mapping each time."""
        mapping = TrackingDict({0: FakeDevice()})
        self.mappings.append(mapping)
        return mapping

    def transmit(self, destination: int, opcode: int, params: bytes) -> bool:
        """Record a raw send."""
        self.calls.append(("transmit", destination, opcode, params))
        return True

    def set_active_source(self) -> bool:
        """Record an active-source claim."""
        self.calls.append(("set_active_source",))
        return True

    def set_stream_path(self, physical: int) -> None:
        """Record a stream-path request."""
        self.calls.append(("set_stream_path", physical))


def test_flavour_tells_the_two_modules_apart() -> None:
    """The whole driver hinges on this distinction being made correctly."""
    assert flavour(FakeSwigModule()) == "swig"
    assert flavour(FakePythonCecModule()) == "python-cec"


def test_an_unrecognised_cec_module_is_refused_not_guessed_at() -> None:
    """Guessing at an unknown API would fail later and more confusingly."""
    with pytest.raises(DriverUnavailable, match="neither"):
        flavour(object())


def test_create_driver_picks_the_matching_implementation() -> None:
    """Each module shape gets the driver written for it."""
    assert isinstance(create_driver(None, FakeSwigModule()), SwigDriver)
    assert isinstance(create_driver(None, FakePythonCecModule()), PythonCecDriver)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, "0.0.0.0"), (0x1000, "1.0.0.0"), (0x1100, "1.1.0.0"), (0x4321, "4.3.2.1")],
)
def test_physical_addresses_render_the_documented_way(
    value: int, expected: str
) -> None:
    """CEC documentation writes physical addresses as four nibbles."""
    assert format_physical_address(value) == expected


def test_swig_device_name_fits_libcecs_field() -> None:
    """strDeviceName is char[13] and SWIG refuses anything longer outright."""
    module = FakeSwigModule()
    SwigDriver(module).detect_adapters()
    assert len(module.config.strDeviceName) < 13


def test_swig_open_accepts_a_ping_when_open_reports_failure() -> None:
    """libcec reports failure on adapters that work; a ping settles it."""
    lib = FakeLib(open_ok=False, ping_ok=True)
    driver = SwigDriver(FakeSwigModule(lib), "/dev/ttyACM0")
    driver.open()
    assert not lib.closed


def test_swig_open_gives_up_when_the_adapter_never_answers() -> None:
    """A genuinely absent adapter must fail rather than retry forever."""
    lib = FakeLib(open_ok=False, ping_ok=False)
    driver = SwigDriver(FakeSwigModule(lib), "/dev/ttyACM0")
    with pytest.raises(DriverUnavailable, match="could not open"):
        driver.open()
    assert lib.closed


def test_swig_sends_keys_natively_rather_than_by_hand() -> None:
    """libcec handles the press/release timing better than we would."""
    lib = FakeLib()
    driver = SwigDriver(FakeSwigModule(lib), "/dev/ttyACM0")
    driver.open()
    assert driver.send_key(0, 0x53)
    assert ("SendKeypress", 0, 0x53) in lib.calls
    assert ("SendKeyRelease", 0) in lib.calls


def test_swig_raw_transmit_builds_a_cec_client_style_frame() -> None:
    """The frame names our own logical address as the initiator."""
    lib = FakeLib()
    driver = SwigDriver(FakeSwigModule(lib), "/dev/ttyACM0")
    driver.open()
    driver.transmit(0x0F, 0x82, b"\x10\x00")
    assert ("Transmit", "1F:82:10:00") in lib.calls


def test_swig_scan_reports_only_devices_that_answered() -> None:
    """An address nobody holds must not appear as a device."""
    driver = SwigDriver(FakeSwigModule(), "/dev/ttyACM0")
    driver.open()
    devices = driver.scan()
    assert [device["address"] for device in devices] == [0]
    assert devices[0]["physical_address"] == "1.0.0.0"
    assert devices[0]["vendor"] == "001582"
    assert devices[0]["is_on"] is True


def test_python_cec_scan_clears_every_device_mapping() -> None:
    """A surviving Device aborts the process at shutdown. This is the guard."""
    module = FakePythonCecModule()
    driver = PythonCecDriver(module, "/dev/ttyACM0")
    driver.open()
    driver.scan()
    assert module.mappings
    assert all(mapping.cleared for mapping in module.mappings)


def test_python_cec_power_on_clears_its_mapping_too() -> None:
    """power_on is the one place a Device is unavoidable; it still goes."""
    module = FakePythonCecModule()
    driver = PythonCecDriver(module, "/dev/ttyACM0")
    driver.open()
    assert driver.power(0, True)
    assert all(mapping.cleared for mapping in module.mappings)


def test_python_cec_standby_creates_no_device_at_all() -> None:
    """Standby is a plain opcode, so it need not touch list_devices."""
    module = FakePythonCecModule()
    driver = PythonCecDriver(module, "/dev/ttyACM0")
    driver.open()
    assert driver.power(0, False)
    assert not module.mappings
    assert ("transmit", 0, 0x36, b"") in module.calls


def test_python_cec_sends_keys_as_press_and_release() -> None:
    """Without native key support, the two messages are built by hand."""
    module = FakePythonCecModule()
    driver = PythonCecDriver(module, "/dev/ttyACM0")
    driver.open()
    assert driver.send_key(0, 0x53, hold=0)
    opcodes = [call[2] for call in module.calls if call[0] == "transmit"]
    assert opcodes == [0x44, 0x45]
