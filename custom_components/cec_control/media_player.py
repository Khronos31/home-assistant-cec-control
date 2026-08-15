"""Television control for CEC Control."""

from __future__ import annotations

import time
from typing import Any, ClassVar

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .backend import BackendError
from .const import (
    ADDRESS_BROADCAST,
    ADDRESS_TV,
    CONF_DEVICE_ADDRESS,
    OPCODE_ACTIVE_SOURCE,
    OPTIMISTIC_STATE_SECONDS,
    SOURCES,
)
from .coordinator import CecConfigEntry, CecCoordinator
from .entity import CecEntity
from .keymap import digits_to_keys, resolve_key


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CecConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the television for one config entry."""
    address = int(entry.data.get(CONF_DEVICE_ADDRESS, ADDRESS_TV))
    async_add_entities([CecMediaPlayer(entry.runtime_data, address)])


class CecMediaPlayer(CecEntity, MediaPlayerEntity):
    """A television reached over CEC."""

    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.TV
    _attr_source_list: ClassVar[list[str]] = list(SOURCES)
    _attr_supported_features = (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
    )

    def __init__(self, coordinator: CecCoordinator, address: int) -> None:
        """Set up optimistic state tracking alongside the entity."""
        super().__init__(coordinator, address)
        self._optimistic_state: MediaPlayerState | None = None
        self._optimistic_until = 0.0

    @property
    def state(self) -> MediaPlayerState | None:
        """Return on or off.

        A television takes a while to admit it has changed: on this house's set,
        power-on shows up after about a second but standby only after eight. For
        a short window after a command the commanded state is reported, so the
        UI does not visibly snap back before the set catches up.
        """
        if self._optimistic_state is not None:
            if time.monotonic() < self._optimistic_until:
                return self._optimistic_state
            self._optimistic_state = None
        device = self.device
        if device is None or device.is_on is None:
            return None
        return MediaPlayerState.ON if device.is_on else MediaPlayerState.OFF

    def _assume(self, state: MediaPlayerState) -> None:
        """Report one state until the bus confirms it."""
        self._optimistic_state = state
        self._optimistic_until = time.monotonic() + OPTIMISTIC_STATE_SECONDS
        self.async_write_ha_state()

    async def _run(self, action: Any) -> None:
        """Await one backend call, translating failures for the UI."""
        try:
            await action
        except BackendError as err:
            raise HomeAssistantError(str(err)) from err

    async def async_turn_on(self) -> None:
        """Turn the television on."""
        await self._run(self.coordinator.backend.async_power(self._address, True))
        self._assume(MediaPlayerState.ON)

    async def async_turn_off(self) -> None:
        """Put the television into standby."""
        await self._run(self.coordinator.backend.async_power(self._address, False))
        self._assume(MediaPlayerState.OFF)

    async def async_select_source(self, source: str) -> None:
        """Switch the television to one of its inputs."""
        try:
            physical = SOURCES[source]
        except KeyError:
            raise ServiceValidationError(
                f"unknown source {source!r}; expected one of {', '.join(SOURCES)}"
            ) from None
        await self._run(
            self.coordinator.backend.async_transmit(
                ADDRESS_BROADCAST, OPCODE_ACTIVE_SOURCE, bytes(physical)
            )
        )
        self._attr_source = source
        self.async_write_ha_state()

    async def async_volume_up(self) -> None:
        """Raise the volume by one step."""
        await self._send_key("volume_up")

    async def async_volume_down(self) -> None:
        """Lower the volume by one step."""
        await self._send_key("volume_down")

    async def async_mute_volume(self, mute: bool) -> None:
        """Toggle mute. CEC offers a toggle only, so `mute` is not honoured."""
        await self._send_key("mute")

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Tune to a channel by typing its number on the remote."""
        if media_type not in (MediaType.CHANNEL, "channel"):
            raise ServiceValidationError(
                f"cec_control can only play media_type 'channel', got {media_type!r}"
            )
        try:
            channel = int(str(media_id).strip())
        except ValueError:
            raise ServiceValidationError(
                f"channel must be a number, got {media_id!r}"
            ) from None
        try:
            codes = digits_to_keys(channel)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        for code in codes:
            await self._run(
                self.coordinator.backend.async_send_key(self._address, code)
            )

    async def _send_key(self, name: str) -> None:
        """Send one named key to this device."""
        await self._run(
            self.coordinator.backend.async_send_key(self._address, resolve_key(name))
        )
