"""CEC user-control key names for this integration.

The codes are CEC "user control" codes carried in the payload of a
USER_CONTROL_PRESSED message. They are transport independent, so this module
imports neither Home Assistant nor libcec and can be unit tested on its own.

Provenance: the mapping was lifted from Khronos31/lua-remote-hub's
`ha-addon/config/HDMI-CEC.lua`, where it had already been verified against the
television in this house, and re-checked here on 2026-08-16. That file records
its entries as raw frames like `10:44:21` — source 1 (the Pulse-Eight adapter's
logical address), destination 0 (the TV), opcode 0x44 (USER_CONTROL_PRESSED),
payload 0x21. Only the payload byte is kept here; the frame is rebuilt by the
backend, which knows its own source address.

Keys that the same file records as *not* working on this television are listed
in UNSUPPORTED_KEYS rather than deleted, so nobody spends an evening
rediscovering that the TV ignores them.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

# CEC user control codes. Names are the strings accepted by remote.send_command.
KEY_CODES: Final[MappingProxyType[str, int]] = MappingProxyType(
    {
        # Cursor and selection.
        "enter": 0x00,
        "up": 0x01,
        "down": 0x02,
        "left": 0x03,
        "right": 0x04,
        # Menus.
        "setting": 0x0A,
        "rec_list": 0x0B,
        "return": 0x0D,
        # Digits. The CEC spec assigns 0x20 to "number 0", so a decimal digit d
        # is 0x20 + d. A Japanese remote's "10" key is the same code as 0.
        "num_0": 0x20,
        "num_1": 0x21,
        "num_2": 0x22,
        "num_3": 0x23,
        "num_4": 0x24,
        "num_5": 0x25,
        "num_6": 0x26,
        "num_7": 0x27,
        "num_8": 0x28,
        "num_9": 0x29,
        # Channel and input.
        "channel_up": 0x30,
        "channel_down": 0x31,
        "input_select": 0x34,
        "screen_display": 0x35,
        # Volume.
        "volume_up": 0x41,
        "volume_down": 0x42,
        "mute": 0x43,
        # Transport.
        "play": 0x44,
        "stop": 0x45,
        "pause": 0x46,
        "rewind": 0x48,
        "fast_forward": 0x49,
        "next": 0x4B,
        "previous": 0x4C,
        # Broadcast extras.
        "guide": 0x53,
        "blue": 0x71,
        "red": 0x72,
        "green": 0x73,
        "yellow": 0x74,
        "data": 0x76,
        # Discrete power. A real remote only has a toggle; these two are CEC
        # only, which is precisely why they are worth having.
        "power_on": 0x6D,
        "power_off": 0x6C,
    }
)

# Recorded as tried and ignored by the television in this house. Kept as
# documentation; sending them is allowed through the raw service but they are
# not offered as named keys.
UNSUPPORTED_KEYS: Final[tuple[str, ...]] = (
    "power",  # toggle; the TV only honours the discrete power_on/power_off
    "mode_digital",
    "mode_bs",
    "mode_cs",
    "menu",
    "exit",
    "program_info",
    "subtitle",
    "audio_select",
    "back_10s",
    "skip_30s",
)


class UnknownKeyError(ValueError):
    """Raised for a key name that is not in KEY_CODES."""


def resolve_key(name: str) -> int:
    """Return the CEC user control code for one key name.

    Accepts the canonical lowercase name, and is tolerant of the casing and
    hyphenation people actually type in automations.
    """
    candidate = str(name).strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return KEY_CODES[candidate]
    except KeyError:
        if candidate in UNSUPPORTED_KEYS:
            raise UnknownKeyError(
                f"{name!r} is a CEC key this television ignores; "
                "see UNSUPPORTED_KEYS in keymap.py"
            ) from None
        raise UnknownKeyError(
            f"unknown key {name!r}; expected one of {', '.join(sorted(KEY_CODES))}"
        ) from None


def digits_to_keys(channel: int) -> list[int]:
    """Return the key codes that type one channel number on the remote."""
    if not isinstance(channel, int) or isinstance(channel, bool) or channel < 0:
        raise ValueError(f"channel must be a non-negative integer, got {channel!r}")
    return [0x20 + int(digit) for digit in str(channel)]
