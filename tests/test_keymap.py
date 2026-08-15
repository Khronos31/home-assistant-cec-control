"""Tests for the CEC key table."""

from __future__ import annotations

import pytest

from custom_components.cec_control.keymap import (
    KEY_CODES,
    UNSUPPORTED_KEYS,
    UnknownKeyError,
    digits_to_keys,
    resolve_key,
)


def test_every_code_is_one_byte() -> None:
    """A user control code travels as a single payload byte."""
    assert all(0 <= code <= 0xFF for code in KEY_CODES.values())


def test_codes_are_unique_per_name() -> None:
    """Two names may not silently share meaning by accident."""
    duplicates = {
        code for code in KEY_CODES.values() if list(KEY_CODES.values()).count(code) > 1
    }
    assert not duplicates, f"duplicate codes: {[hex(code) for code in duplicates]}"


def test_digits_follow_the_cec_numbering() -> None:
    """The spec puts 'number 0' at 0x20, so digit d is 0x20 + d."""
    for digit in range(10):
        assert KEY_CODES[f"num_{digit}"] == 0x20 + digit


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("guide", 0x53),
        ("GUIDE", 0x53),
        ("volume-up", 0x41),
        ("  Volume Up  ", 0x41),
        ("power_off", 0x6C),
    ],
)
def test_resolve_key_is_tolerant_of_how_people_type(
    written: str, expected: int
) -> None:
    """Automations get written by hand; accept the obvious variants."""
    assert resolve_key(written) == expected


def test_unknown_key_lists_the_alternatives() -> None:
    """An unknown key should not send someone hunting through source."""
    with pytest.raises(UnknownKeyError, match="guide"):
        resolve_key("nonexistent")


def test_known_unsupported_key_says_so() -> None:
    """A key this television ignores gets its own explanation."""
    with pytest.raises(UnknownKeyError, match="ignores"):
        resolve_key("menu")


def test_unsupported_keys_are_not_also_offered() -> None:
    """A key cannot be both supported and recorded as unsupported."""
    assert not set(UNSUPPORTED_KEYS) & set(KEY_CODES)


@pytest.mark.parametrize(
    ("channel", "expected"),
    [
        (8, [0x28]),
        (0, [0x20]),
        (12, [0x21, 0x22]),
        (101, [0x21, 0x20, 0x21]),
    ],
)
def test_digits_to_keys(channel: int, expected: list[int]) -> None:
    """A channel number is typed one digit at a time."""
    assert digits_to_keys(channel) == expected


@pytest.mark.parametrize("bad", [-1, True, "8", 1.5])
def test_digits_to_keys_rejects_nonsense(bad: object) -> None:
    """A negative or non-integer channel is a caller error, not a CEC error."""
    with pytest.raises(ValueError, match="non-negative integer"):
        digits_to_keys(bad)  # type: ignore[arg-type]
