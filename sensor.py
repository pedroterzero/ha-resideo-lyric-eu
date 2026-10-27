"""Sensors for Resideo Lyric EU."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Thermostat
from .coordinator import ResideoConfigEntry
from .entity import ResideoEntity

SETPOINT_STATUSES = [
    "no_hold",
    "temporary_hold",
    "hold_until",
    "permanent_hold",
    "vacation_hold",
]


def _snake(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


def _setpoint_status(dev: Thermostat) -> str | None:
    status = _snake(dev.setpoint_status)
    return status if status in SETPOINT_STATUSES else None


def _hold_attrs(dev: Thermostat) -> dict[str, Any]:
    # Anything beyond the core changeable values (e.g. a hold-until time).
    core = {"mode", "heatSetpoint", "coolSetpoint", "thermostatSetpointStatus", "heatCoolMode"}
    return {
        "raw_status": dev.setpoint_status,
        **{k: v for k, v in dev.changeable.items() if k not in core},
    }


@dataclass(frozen=True, kw_only=True)
class ResideoSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Thermostat], Any]
    attrs_fn: Callable[[Thermostat], dict[str, Any]] | None = None
    temperature: bool = False


SENSORS: tuple[ResideoSensorDescription, ...] = (
    ResideoSensorDescription(
        key="setpoint_status",
        translation_key="setpoint_status",
        device_class=SensorDeviceClass.ENUM,
        options=SETPOINT_STATUSES,
        value_fn=_setpoint_status,
        attrs_fn=_hold_attrs,
    ),
    ResideoSensorDescription(
        key="schedule_status",
        translation_key="schedule_status",
        value_fn=lambda d: d.schedule_status,
    ),
    ResideoSensorDescription(
        key="schedule_period",
        translation_key="schedule_period",
        value_fn=lambda d: d.schedule_period.get("period"),
        attrs_fn=lambda d: {"day": d.schedule_period.get("day")},
    ),
    ResideoSensorDescription(
        key="schedule_type",
        translation_key="schedule_type",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.schedule_type,
        attrs_fn=lambda d: {
            "available_schedule_types": (d.raw.get("scheduleCapabilities") or {}).get(
                "availableScheduleTypes"
            )
        },
    ),
    ResideoSensorDescription(
        key="heat_demand",
        translation_key="heat_demand",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.demand.get("demand"),
        attrs_fn=lambda d: {
            "current_stage": d.demand.get("currentStage"),
            "demand_mode": d.demand.get("mode"),
        },
    ),
    ResideoSensorDescription(
        key="indoor_temperature",
        translation_key="indoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        temperature=True,
        value_fn=lambda d: d.indoor_temperature,
    ),
    ResideoSensorDescription(
        key="outdoor_temperature",
        translation_key="outdoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        temperature=True,
        value_fn=lambda d: d.outdoor_temperature,
    ),
    ResideoSensorDescription(
        key="outdoor_humidity",
        translation_key="outdoor_humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda d: d.outdoor_humidity,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ResideoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        ResideoSensor(coordinator, device_id, description)
        for device_id in coordinator.data
        for description in SENSORS
    )


class ResideoSensor(ResideoEntity, SensorEntity):
    entity_description: ResideoSensorDescription

    def __init__(self, coordinator, device_id: str, description: ResideoSensorDescription) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.entity_description.temperature:
            return (
                UnitOfTemperature.CELSIUS
                if self.device.celsius
                else UnitOfTemperature.FAHRENHEIT
            )
        return self.entity_description.native_unit_of_measurement

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.device)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.device)
