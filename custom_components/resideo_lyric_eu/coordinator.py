"""Polling coordinator for Resideo Lyric EU."""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ResideoAuthError, ResideoClient, ResideoError, Thermostat, thermostats
from .const import DOMAIN, FOLLOWUP_REFRESH, SCAN_INTERVAL, SCHEDULE_INTERVAL

_LOGGER = logging.getLogger(__name__)

type ResideoConfigEntry = ConfigEntry[ResideoCoordinator]


class ResideoCoordinator(DataUpdateCoordinator[dict[str, Thermostat]]):
    """Fetch all locations (devices embedded) in one CHIL call."""

    config_entry: ResideoConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ResideoConfigEntry, client: ResideoClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        # Raw timed schedules per device; polled less often than state.
        self.schedules: dict[str, dict[str, Any]] = {}
        self._schedules_at: datetime | None = None
        self._followup: CALLBACK_TYPE | None = None
        entry.async_on_unload(self._cancel_followup)

    def _cancel_followup(self) -> None:
        if self._followup:
            self._followup()
            self._followup = None

    async def async_write(self, call: Awaitable[Any], schedule: bool = False) -> None:
        """Run a write, then refresh now and again once the device has synced.

        schedule=True also re-reads the schedules on the next refresh.
        """
        try:
            await call
        except ResideoAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(f"Honeywell Home sign-in expired: {err}") from err
        except ResideoError as err:
            raise HomeAssistantError(
                f"Honeywell Home rejected the change: {err}"
            ) from err
        if schedule:
            self._schedules_at = None
        await self.async_refresh()
        self._cancel_followup()

        async def _followup(_now: Any) -> None:
            self._followup = None
            await self.async_request_refresh()

        self._followup = async_call_later(self.hass, FOLLOWUP_REFRESH, _followup)

    async def _async_update_data(self) -> dict[str, Thermostat]:
        try:
            locations = await self.client.async_get_locations()
        except ResideoAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ResideoError as err:
            raise UpdateFailed(str(err)) from err
        devices = thermostats(locations)
        now = dt_util.utcnow()
        if self._schedules_at is None or now - self._schedules_at >= SCHEDULE_INTERVAL:
            await self._async_update_schedules(devices)
            self._schedules_at = now
        return devices

    async def _async_update_schedules(self, devices: dict[str, Thermostat]) -> None:
        """Best effort: a failed schedule read keeps the previous one."""
        for device_id, dev in devices.items():
            try:
                self.schedules[device_id] = await self.client.async_get_schedule(dev)
            except ResideoAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except ResideoError as err:
                _LOGGER.warning("Reading the schedule of %s failed: %s", device_id, err)
