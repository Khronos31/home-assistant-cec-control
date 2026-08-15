"""Key sending for CEC Control."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from homeassistant.components.remote import (
    ATTR_DELAY_SECS,
    ATTR_NUM_REPEATS,
    DEFAULT_DELAY_SECS,
    RemoteEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .backend import BackendError
from .const import ADDRESS_TV, CONF_DEVICE_ADDRESS
from .coordinator import CecConfigEntry, CecCoordinator
from .entity import CecEntity
from .keymap import KEY_CODES, UnknownKeyError, resolve_key

RAW_PREFIX = "raw:"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CecConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the remote for one config entry."""
    address = int(entry.data.get(CONF_DEVICE_ADDRESS, ADDRESS_TV))
    async_add_entities([CecRemote(entry.runtime_data, address)])


def parse_raw(command: str) -> tuple[int, bytes]:
    """Parse a ``raw:<opcode>[:<params>]`` command into an opcode and payload.

    The destination is the entity's own device, and the source is whatever
    logical address the adapter holds, so a raw command names neither. That
    keeps the escape hatch from depending on which adapter is in use.
    """
    body = command[len(RAW_PREFIX) :].strip()
    if not body:
        raise ServiceValidationError(
            "raw commands look like 'raw:82' or 'raw:82:10:00'"
        )
    parts = [part for part in body.replace(" ", ":").split(":") if part]
    try:
        numbers = [int(part, 16) for part in parts]
    except ValueError:
        raise ServiceValidationError(
            f"raw command {command!r} must be hexadecimal bytes"
        ) from None
    if any(number < 0 or number > 0xFF for number in numbers):
        raise ServiceValidationError(f"raw command {command!r} has a byte out of range")
    return numbers[0], bytes(numbers[1:])


class CecRemote(CecEntity, RemoteEntity):
    """Send CEC key presses to one device."""

    _attr_name = "Remote"

    def __init__(self, coordinator: CecCoordinator, address: int) -> None:
        """Set up the remote."""
        super().__init__(coordinator, address)

    @property
    def is_on(self) -> bool:
        """Return whether the target device reports itself as on."""
        device = self.device
        return bool(device is not None and device.is_on)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the key names this remote accepts."""
        return {"known_keys": sorted(KEY_CODES)}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the target device on."""
        await self._run(self.coordinator.backend.async_power(self._address, True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Put the target device into standby."""
        await self._run(self.coordinator.backend.async_power(self._address, False))

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        """Send one or more keys.

        Each command is a key name from ``keymap.KEY_CODES`` (``guide``,
        ``num_3``, ``volume_up`` …) or a raw escape hatch of the form
        ``raw:<opcode>[:<params>]`` for anything the key table does not name.
        """
        repeats = int(kwargs.get(ATTR_NUM_REPEATS) or 1)
        delay = float(kwargs.get(ATTR_DELAY_SECS) or DEFAULT_DELAY_SECS)
        commands = list(command)
        backend = self.coordinator.backend

        for repeat in range(repeats):
            for index, raw_command in enumerate(commands):
                text = str(raw_command).strip()
                if text.lower().startswith(RAW_PREFIX):
                    opcode, params = parse_raw(text.lower())
                    await self._run(
                        backend.async_transmit(self._address, opcode, params)
                    )
                else:
                    try:
                        code = resolve_key(text)
                    except UnknownKeyError as err:
                        raise ServiceValidationError(str(err)) from err
                    await self._run(backend.async_send_key(self._address, code))
                is_last = repeat == repeats - 1 and index == len(commands) - 1
                if not is_last:
                    await asyncio.sleep(delay)

    async def _run(self, action: Any) -> None:
        """Await one backend call, translating failures for the UI."""
        try:
            await action
        except BackendError as err:
            raise HomeAssistantError(str(err)) from err
