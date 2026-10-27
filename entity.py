"""Base entity for Resideo Lyric EU."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Thermostat
from .const import DOMAIN
from .coordinator import ResideoCoordinator


class ResideoEntity(CoordinatorEntity[ResideoCoordinator]):
    """Entity bound to one thermostat."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ResideoCoordinator, device_id: str, key: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}_{key}"
        dev = coordinator.data[device_id]
        # No MAC `connections`: the same T6 is also paired via HomeKit
        # Controller and must stay a separate device there.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=dev.name,
            manufacturer="Resideo",
            model=f"T6 ({dev.raw.get('deviceType', 'thermostat')}, cloud)",
            sw_version=dev.raw.get("deviceOsVersion"),
            serial_number=dev.raw.get("deviceSerialNo"),
        )

    @property
    def device(self) -> Thermostat:
        return self.coordinator.data[self._device_id]

    @property
    def available(self) -> bool:
        return super().available and self._device_id in self.coordinator.data
