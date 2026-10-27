"""Diagnostics for Resideo Lyric EU."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL
from homeassistant.core import HomeAssistant

from .const import CONF_REFRESH_TOKEN
from .coordinator import ResideoConfigEntry

TO_REDACT = {CONF_EMAIL, CONF_REFRESH_TOKEN, "macID", "deviceSerialNo"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ResideoConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "devices": {
            device_id: {
                "location_id": dev.location_id,
                "raw": async_redact_data(dev.raw, TO_REDACT),
            }
            for device_id, dev in coordinator.data.items()
        },
    }
