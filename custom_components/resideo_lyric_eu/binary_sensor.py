"""Binary sensors for Resideo Lyric EU."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import ResideoConfigEntry
from .entity import ResideoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ResideoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    for device_id in coordinator.data:
        entities.append(ResideoHolidaySensor(coordinator, device_id))
        entities.append(ResideoOnlineSensor(coordinator, device_id))
    async_add_entities(entities)


class ResideoHolidaySensor(ResideoEntity, BinarySensorEntity):
    """Holiday / away (CHIL vacationHold) in effect right now."""

    _attr_translation_key = "holiday"

    def __init__(self, coordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, "holiday")

    @property
    def is_on(self) -> bool:
        return self.device.holiday_active(dt_util.utcnow())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        dev = self.device
        start, end = dev.vacation_start, dev.vacation_end
        return {
            "booked": dev.vacation_enabled,
            "start": dt_util.as_local(start).isoformat() if start else None,
            "end": dt_util.as_local(end).isoformat() if end else None,
            "temperature": dev.vacation_heat_setpoint,
        }


class ResideoOnlineSensor(ResideoEntity, BinarySensorEntity):
    """Cloud reports the thermostat as connected."""

    _attr_translation_key = "online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, "online")

    @property
    def is_on(self) -> bool:
        return self.device.is_alive
