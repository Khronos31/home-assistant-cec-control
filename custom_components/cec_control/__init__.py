"""The CEC Control integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .backend import BackendError, CecBackend, DaemonBackend, LocalBackend
from .const import BACKEND_DAEMON, CONF_ADAPTER, CONF_BACKEND, DEFAULT_DAEMON_PORT
from .coordinator import CecConfigEntry, CecCoordinator

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER, Platform.REMOTE]


def build_backend(hass: HomeAssistant, data: dict) -> CecBackend:
    """Return the backend one config entry describes."""
    if data.get(CONF_BACKEND) == BACKEND_DAEMON:
        return DaemonBackend(
            async_get_clientsession(hass),
            data[CONF_HOST],
            int(data.get(CONF_PORT, DEFAULT_DAEMON_PORT)),
        )
    return LocalBackend(hass, data.get(CONF_ADAPTER))


async def async_setup_entry(hass: HomeAssistant, entry: CecConfigEntry) -> bool:
    """Set up one CEC adapter."""
    backend = build_backend(hass, dict(entry.data))
    coordinator = CecCoordinator(hass, entry, backend)
    try:
        await coordinator.async_config_entry_first_refresh()
    except BackendError as err:
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CecConfigEntry) -> bool:
    """Tear one CEC adapter down."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.backend.async_close()
    return unloaded
