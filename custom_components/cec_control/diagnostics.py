"""Diagnostics for CEC Control."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import CecConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CecConfigEntry
) -> dict[str, Any]:
    """Return what is worth knowing when a CEC problem is reported.

    Nothing here is sensitive: CEC carries no credentials, and the bus scan is
    the same information `cec-client` prints to anyone who asks.
    """
    coordinator = entry.runtime_data
    status = coordinator.data
    return {
        "entry": {
            "data": dict(entry.data),
            "title": entry.title,
        },
        "backend": {
            "label": coordinator.backend.label,
            "type": type(coordinator.backend).__name__,
        },
        "last_scan": {
            "available": None if status is None else status.available,
            "devices": (
                []
                if status is None
                else [device.as_dict() for device in status.devices.values()]
            ),
        },
    }
