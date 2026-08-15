"""Shared entity plumbing for CEC Control."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .backend import CecDevice
from .const import DOMAIN
from .coordinator import CecCoordinator


class CecEntity(CoordinatorEntity[CecCoordinator]):
    """One entity backed by a device on the CEC bus."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CecCoordinator, address: int) -> None:
        """Bind this entity to one logical address."""
        super().__init__(coordinator)
        self._address = address
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{address}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{address}")},
            name=entry.title,
            manufacturer="HDMI-CEC",
            model=f"CEC logical address {address}",
        )

    @property
    def device(self) -> CecDevice | None:
        """Return this entity's device as the last scan saw it."""
        data = self.coordinator.data
        return None if data is None else data.devices.get(self._address)

    @property
    def available(self) -> bool:
        """Return whether the last scan reached this device."""
        return super().available and self.device is not None
