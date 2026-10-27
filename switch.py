"""Switches for Resideo Lyric EU."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import schedule_days
from .coordinator import ResideoConfigEntry
from .entity import ResideoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ResideoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        ResideoScheduleSwitch(coordinator, device_id) for device_id in coordinator.data
    )


class ResideoScheduleSwitch(ResideoEntity, SwitchEntity):
    """The thermostat's own schedule: on = running, off = paused."""

    _attr_translation_key = "schedule"

    def __init__(self, coordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, "schedule")

    @property
    def is_on(self) -> bool | None:
        if self.device.schedule_status is None:
            return None
        return not self.device.schedule_paused

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The weekly program, one "HH:MM temp" entry per period."""
        schedule = self.coordinator.schedules.get(self._device_id)
        if not schedule:
            return None
        return {
            day: [f"{p['start']} {p['temperature']}" for p in periods]
            for day, periods in schedule_days(self.device, schedule).items()
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_write(
            self.coordinator.client.async_set_schedule_enabled(self.device, True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_write(
            self.coordinator.client.async_set_schedule_enabled(self.device, False)
        )
