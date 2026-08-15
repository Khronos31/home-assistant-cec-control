"""Bus polling for the CEC Control integration."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .backend import BackendError, BackendStatus, CecBackend
from .const import DOMAIN, SCAN_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)

type CecConfigEntry = ConfigEntry[CecCoordinator]


class CecCoordinator(DataUpdateCoordinator[BackendStatus]):
    """Poll one CEC bus and share the result with every entity above it."""

    def __init__(
        self, hass: HomeAssistant, entry: CecConfigEntry, backend: CecBackend
    ) -> None:
        """Set up polling for one backend."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {backend.label}",
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
            config_entry=entry,
        )
        self.backend = backend

    async def _async_update_data(self) -> BackendStatus:
        """Scan the bus."""
        try:
            return await self.backend.async_scan()
        except BackendError as err:
            raise UpdateFailed(str(err)) from err
