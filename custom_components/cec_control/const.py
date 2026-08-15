"""Constants for the CEC Control integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "cec_control"

# Config entry keys.
CONF_BACKEND: Final = "backend"
CONF_ADAPTER: Final = "adapter"
CONF_HOST: Final = "host"
CONF_PORT: Final = "port"
CONF_DEVICE_ADDRESS: Final = "device_address"

BACKEND_LOCAL: Final = "local"
BACKEND_DAEMON: Final = "daemon"

DEFAULT_DAEMON_PORT: Final = 8080

# CEC logical addresses we care about by name. The full list lives in the CEC
# specification; only the ones this integration references are named here.
ADDRESS_TV: Final = 0
ADDRESS_BROADCAST: Final = 15

# CEC opcodes used by this integration.
OPCODE_ACTIVE_SOURCE: Final = 0x82
OPCODE_STANDBY: Final = 0x36
OPCODE_USER_CONTROL_PRESSED: Final = 0x44
OPCODE_USER_CONTROL_RELEASE: Final = 0x45

# Selectable sources, as the CEC physical address to hand to a broadcast
# ACTIVE_SOURCE message. "TV" (0.0.0.0) hands control back to the television's
# own tuner; the HDMI entries name the television's input ports. Verified in
# this form in Khronos31/lua-remote-hub (`1F:82:10:00` switches to HDMI 1).
SOURCES: Final[dict[str, tuple[int, int]]] = {
    "TV": (0x00, 0x00),
    "HDMI1": (0x10, 0x00),
    "HDMI2": (0x20, 0x00),
    "HDMI3": (0x30, 0x00),
    "HDMI4": (0x40, 0x00),
}

# How long a key is held between PRESSED and RELEASE. libcec's own key handling
# uses a comparable hold; sending RELEASE immediately after PRESSED is accepted
# by the TV in this house but a short hold is closer to a real remote.
KEY_HOLD_SECONDS: Final = 0.2

# Measured on the study TV (MAXZEN J32CH06) on 2026-08-16: power-on is reflected
# in the reported power status after about one second, standby after about eight.
# The media_player reports an optimistic state for this long before trusting the
# polled value again, so the UI does not snap back right after a command.
OPTIMISTIC_STATE_SECONDS: Final = 12.0

# The coordinator scans the bus on this interval. A scan polls every logical
# address, so keep it well clear of the key-repeat timescale.
SCAN_INTERVAL_SECONDS: Final = 30.0
