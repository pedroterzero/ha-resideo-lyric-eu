"""Resideo Lyric EU: Honeywell Home EMEA (CHIL) thermostats via the app login."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.helpers import config_validation as cv, service
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import WEEKDAYS, ResideoClient
from .const import (
    CONF_REFRESH_TOKEN,
    DOMAIN,
    SERVICE_CANCEL_HOLIDAY,
    SERVICE_GET_SCHEDULE,
    SERVICE_SET_HOLD,
    SERVICE_SET_HOLIDAY,
    SERVICE_SET_SCHEDULE,
)
from .coordinator import ResideoConfigEntry, ResideoCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.CLIMATE, Platform.SENSOR, Platform.SWITCH]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PERIOD_SCHEMA = vol.Schema(
    {vol.Required("start"): cv.time, vol.Required("temperature"): vol.Coerce(float)}
)
SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Optional(day.lower()): vol.All(cv.ensure_list, [PERIOD_SCHEMA])
        for day in WEEKDAYS
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the climate entity actions."""
    for name, schema, func in (
        (
            SERVICE_SET_HOLD,
            {
                vol.Optional("temperature"): vol.Coerce(float),
                vol.Optional("until"): cv.time,
            },
            "async_service_set_hold",
        ),
        (
            SERVICE_SET_HOLIDAY,
            {
                vol.Optional("start"): cv.datetime,
                vol.Required("end"): cv.datetime,
                vol.Required("temperature"): vol.Coerce(float),
            },
            "async_service_set_holiday",
        ),
        (SERVICE_CANCEL_HOLIDAY, None, "async_service_cancel_holiday"),
        (
            SERVICE_SET_SCHEDULE,
            {vol.Required("schedule"): SCHEDULE_SCHEMA},
            "async_service_set_schedule",
        ),
    ):
        service.async_register_platform_entity_service(
            hass, DOMAIN, name, entity_domain=CLIMATE_DOMAIN, schema=schema, func=func
        )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_GET_SCHEDULE,
        entity_domain=CLIMATE_DOMAIN,
        schema=None,
        func="async_service_get_schedule",
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ResideoConfigEntry) -> bool:
    """Set up from a config entry."""

    async def _store_refresh_token(token: str) -> None:
        # B2C rotates the refresh token on every use; keep the latest.
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_REFRESH_TOKEN: token}
        )

    client = ResideoClient(
        async_get_clientsession(hass),
        entry.data[CONF_REFRESH_TOKEN],
        _store_refresh_token,
    )
    coordinator = ResideoCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ResideoConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
