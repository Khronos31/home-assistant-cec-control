"""Tests for the remote's command parsing."""

from __future__ import annotations

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.cec_control.remote import parse_raw


@pytest.mark.parametrize(
    ("command", "opcode", "params"),
    [
        ("raw:82", 0x82, b""),
        ("raw:82:10:00", 0x82, b"\x10\x00"),
        ("raw:44 35", 0x44, b"\x35"),
        ("raw:0a", 0x0A, b""),
    ],
)
def test_parse_raw_accepts_the_forms_cec_is_written_in(
    command: str, opcode: int, params: bytes
) -> None:
    """Colons, spaces and bare bytes all appear in CEC documentation."""
    assert parse_raw(command) == (opcode, params)


@pytest.mark.parametrize(
    "command", ["raw:", "raw:   ", "raw:zz", "raw:100", "raw:82:1ff"]
)
def test_parse_raw_rejects_what_cannot_be_a_frame(command: str) -> None:
    """A malformed escape hatch is a caller error with an explanation."""
    with pytest.raises(ServiceValidationError):
        parse_raw(command)
