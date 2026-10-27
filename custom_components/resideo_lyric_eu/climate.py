"""Climate entity for Resideo Lyric EU."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import ResideoError, schedule_days, schedule_with_days
from .const import DOMAIN
from .coordinator import ResideoConfigEntry
from .entity import ResideoEntity

HVAC_MODES = {
    "Heat": HVACMode.HEAT,
    "EmergencyHeat": HVACMode.HEAT,
    "Cool": HVACMode.COOL,
    "Auto": HVACMode.HEAT_COOL,
    "Off": HVACMode.OFF,
}
HVAC_MODES_TO_CHIL = {
    HVACMode.HEAT: "Heat",
    HVACMode.COOL: "Cool",
    HVACMode.HEAT_COOL: "Auto",
    HVACMode.OFF: "Off",
}
HVAC_ACTIONS = {
    "Heat": HVACAction.HEATING,
    "Cool": HVACAction.COOLING,
    "Fan": HVACAction.FAN,
    "EquipmentOff": HVACAction.IDLE,
}

PRESET_SCHEDULE = "schedule"
PRESET_TEMPORARY = "temporary_hold"
PRESET_PERMANENT = "permanent_hold"
PRESET_HOLD_UNTIL = "hold_until"
PRESET_HOLIDAY = "holiday"
STATUS_TO_PRESET = {
    "NoHold": PRESET_SCHEDULE,
    "TemporaryHold": PRESET_TEMPORARY,
    "PermanentHold": PRESET_PERMANENT,
    "HoldUntil": PRESET_HOLD_UNTIL,
    "VacationHold": PRESET_HOLIDAY,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ResideoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        ResideoClimate(coordinator, device_id) for device_id in coordinator.data
    )


class ResideoClimate(ResideoEntity, ClimateEntity):
    """T6 via the CHIL cloud API: setpoint holds, mode, holiday."""

    _attr_name = None
    _attr_translation_key = "thermostat"
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, "climate")

    # --- state ---

    @property
    def temperature_unit(self) -> str:
        return (
            UnitOfTemperature.CELSIUS
            if self.device.celsius
            else UnitOfTemperature.FAHRENHEIT
        )

    @property
    def target_temperature_step(self) -> float:
        return self.device.setpoint_step

    @property
    def hvac_modes(self) -> list[HVACMode]:
        modes = {HVAC_MODES[m] for m in self.device.allowed_modes if m in HVAC_MODES}
        return [m for m in HVACMode if m in modes] or [HVACMode.HEAT]

    @property
    def hvac_mode(self) -> HVACMode | None:
        return HVAC_MODES.get(self.device.mode or "")

    @property
    def hvac_action(self) -> HVACAction | None:
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        return HVAC_ACTIONS.get(self.device.operation_mode or "")

    @property
    def current_temperature(self) -> float | None:
        return self.device.indoor_temperature

    @property
    def target_temperature(self) -> float | None:
        if self.hvac_mode == HVACMode.COOL:
            return self.device.cool_setpoint
        return self.device.heat_setpoint

    @property
    def min_temp(self) -> float:
        return self.device.min_heat_setpoint or super().min_temp

    @property
    def max_temp(self) -> float:
        return self.device.max_heat_setpoint or super().max_temp

    @property
    def _holiday_active(self) -> bool:
        return self.device.holiday_active(dt_util.utcnow())

    @property
    def preset_mode(self) -> str | None:
        if self._holiday_active:
            return PRESET_HOLIDAY
        dev = self.device
        status = dev.setpoint_status or ""
        # Seen live: the T6 turns a TemporaryHold into HoldUntil the next
        # schedule period within seconds.
        if status == "HoldUntil" and (until := dev.changeable.get("holdUntil")) and (
            until == dev.changeable.get("nextPeriodTime")
        ):
            return PRESET_TEMPORARY
        return STATUS_TO_PRESET.get(status)

    @property
    def preset_modes(self) -> list[str]:
        modes = [PRESET_SCHEDULE, PRESET_TEMPORARY, PRESET_PERMANENT]
        current = self.preset_mode
        if current in (PRESET_HOLD_UNTIL, PRESET_HOLIDAY):
            modes.append(current)
        return modes

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        dev = self.device
        return {
            "setpoint_status": dev.setpoint_status,
            "schedule_status": dev.schedule_status,
            "holiday_booked": dev.vacation_enabled,
        }

    # --- control ---

    def _require_no_holiday(self) -> None:
        if self._holiday_active:
            raise ServiceValidationError(
                "A holiday is active on the thermostat; cancel it first "
                f"({DOMAIN}.cancel_holiday or preset 'schedule')."
            )

    def _require_schedule_running(self) -> None:
        # Seen live: with the schedule paused on the T6, a NoHold, TemporaryHold
        # or HoldUntil write is accepted but the device reverts to
        # PermanentHold within seconds.
        if self.device.schedule_paused:
            raise ServiceValidationError(
                "The schedule is paused on the thermostat; choose preset "
                "'schedule' (or turn on the Schedule switch) first."
            )

    def _check_setpoint(self, value: float) -> float:
        if not self.min_temp <= value <= self.max_temp:
            raise ServiceValidationError(
                f"{value} is outside {self.min_temp}..{self.max_temp}"
            )
        step = self.target_temperature_step
        return round(round(value / step) * step, 1)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            await self.async_set_hvac_mode(mode)
        if (value := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        self._require_no_holiday()
        value = self._check_setpoint(value)
        dev = self.device
        status = dev.setpoint_status
        hold_until = (dev.changeable.get("holdUntil") or "")[:5]  # read "HH:MM:SS"
        if status == "PermanentHold" or dev.schedule_paused:
            status = "PermanentHold"
        elif status == "HoldUntil" and hold_until:
            pass
        else:
            # Following the schedule (or a temporary hold): like the app,
            # hold until the next schedule period.
            status, hold_until = "TemporaryHold", None
        await self.coordinator.async_write(
            self.coordinator.client.async_set_heat_setpoint(
                dev, status, value, hold_until
            )
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        mode = HVAC_MODES_TO_CHIL.get(hvac_mode)
        if mode not in self.device.allowed_modes:
            raise ServiceValidationError(f"Mode {hvac_mode} not supported by the thermostat")
        await self.coordinator.async_write(
            self.coordinator.client.async_set_mode(self.device, mode)
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        dev = self.device
        client = self.coordinator.client
        if preset_mode == PRESET_SCHEDULE:
            if self._holiday_active:
                await self.coordinator.async_write(client.async_cancel_holiday(dev))
                return
            if dev.schedule_paused:
                # Resuming also drops the hold: the T6 goes to NoHold itself.
                await self.coordinator.async_write(
                    client.async_set_schedule_enabled(dev, True)
                )
                return
            await self.coordinator.async_write(
                client.async_set_heat_setpoint(dev, "NoHold")
            )
        elif preset_mode in (PRESET_TEMPORARY, PRESET_PERMANENT):
            self._require_no_holiday()
            if preset_mode == PRESET_TEMPORARY:
                self._require_schedule_running()
            setpoint = self.target_temperature
            if setpoint is None:
                raise ServiceValidationError("No current setpoint to hold")
            status = "TemporaryHold" if preset_mode == PRESET_TEMPORARY else "PermanentHold"
            await self.coordinator.async_write(
                client.async_set_heat_setpoint(dev, status, setpoint)
            )
        else:
            raise ServiceValidationError(
                f"Use {DOMAIN}.set_hold or {DOMAIN}.set_holiday for '{preset_mode}'"
            )

    # --- entity services ---

    def _round_time(self, value: datetime) -> datetime:
        step = timedelta(minutes=self.device.time_increment)
        value = value.replace(second=0, microsecond=0)
        rem = timedelta(minutes=value.minute % self.device.time_increment)
        return value - rem + (step if rem >= step / 2 else timedelta())

    async def async_service_set_hold(
        self, temperature: float | None = None, until: time | None = None
    ) -> None:
        self._require_no_holiday()
        value = self._check_setpoint(
            temperature if temperature is not None else (self.target_temperature or 0)
        )
        if until is None:
            status, hold_until = "PermanentHold", None
        else:
            self._require_schedule_running()
            when = self._round_time(datetime.combine(dt_util.now().date(), until))
            status, hold_until = "HoldUntil", when.strftime("%H:%M")
        await self.coordinator.async_write(
            self.coordinator.client.async_set_heat_setpoint(
                self.device, status, value, hold_until
            )
        )

    async def async_service_set_holiday(
        self, end: datetime, temperature: float, start: datetime | None = None
    ) -> None:
        start_utc = dt_util.as_utc(start) if start else dt_util.utcnow()
        end_utc = dt_util.as_utc(end)
        start_utc, end_utc = self._round_time(start_utc), self._round_time(end_utc)
        if end_utc <= max(start_utc, dt_util.utcnow()):
            raise ServiceValidationError("Holiday end must be after its start and in the future")
        value = self._check_setpoint(temperature)
        await self.coordinator.async_write(
            self.coordinator.client.async_set_holiday(
                self.device, start_utc, end_utc, value
            )
        )

    async def async_service_cancel_holiday(self) -> None:
        if not self.device.vacation_enabled:
            raise ServiceValidationError("No holiday booked")
        await self.coordinator.async_write(
            self.coordinator.client.async_cancel_holiday(self.device)
        )

    async def _async_read_schedule(self) -> dict[str, Any]:
        try:
            schedule = await self.coordinator.client.async_get_schedule(self.device)
        except ResideoError as err:
            raise HomeAssistantError(f"Reading the schedule failed: {err}") from err
        self.coordinator.schedules[self._device_id] = schedule
        return schedule

    async def async_service_get_schedule(self) -> dict[str, Any]:
        return {"schedule": schedule_days(self.device, await self._async_read_schedule())}

    async def async_service_set_schedule(
        self, schedule: dict[str, list[dict[str, Any]]]
    ) -> None:
        """Replace the periods of the given days; other days stay as they are."""
        increment = self.device.time_increment
        days: dict[str, list[dict[str, Any]]] = {}
        for day, periods in schedule.items():
            starts = [p["start"] for p in periods]
            if starts != sorted(set(starts)):
                raise ServiceValidationError(f"{day}: start times must be increasing and unique")
            for start in starts:
                if start.minute % increment or start.second:
                    raise ServiceValidationError(
                        f"{day}: {start:%H:%M} is not a multiple of {increment} minutes"
                    )
            days[day] = [
                {"start": p["start"].strftime("%H:%M"), "temperature": self._check_setpoint(p["temperature"])}
                for p in periods
            ]
        current = await self._async_read_schedule()
        await self.coordinator.async_write(
            self.coordinator.client.async_set_schedule(
                self.device, schedule_with_days(self.device, current, days)
            ),
            schedule=True,
        )
