"""Config flow for Resideo Lyric EU."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
    async_get_clientsession,
)

from .api import (
    ResideoAuthError,
    ResideoClient,
    ResideoConnectionError,
    ResideoError,
    async_login,
    login_cookie_jar,
    thermostats,
)
from .const import CONF_REFRESH_TOKEN, DOMAIN

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str}
)
REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


class ResideoLyricEuConfigFlow(ConfigFlow, domain=DOMAIN):
    """Sign in with the Honeywell Home app account; store only the refresh token."""

    VERSION = 1

    async def _async_sign_in(
        self, email: str, password: str, errors: dict[str, str]
    ) -> str | None:
        """Return a refresh token, or None with `errors` filled in."""
        # Private cookie jar for the B2C transaction; detach so the shared
        # connector survives.
        login_session = async_create_clientsession(
            self.hass, auto_cleanup=False, cookie_jar=login_cookie_jar()
        )
        try:
            tokens = await async_login(login_session, email, password)
            client = ResideoClient.from_token_response(
                async_get_clientsession(self.hass), tokens
            )
            if not thermostats(await client.async_get_locations()):
                errors["base"] = "no_thermostats"
                return None
        except ResideoAuthError as err:
            _LOGGER.debug("sign-in rejected: %s", err)
            errors["base"] = "invalid_auth"
            return None
        except ResideoConnectionError as err:
            _LOGGER.debug("sign-in connection error: %s", err)
            errors["base"] = "cannot_connect"
            return None
        except ResideoError:
            _LOGGER.exception("unexpected error during sign-in")
            errors["base"] = "unknown"
            return None
        finally:
            login_session.detach()
        # The token response refresh token may have been rotated by the probe.
        return client.refresh_token

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()
            refresh_token = await self._async_sign_in(
                email, user_input[CONF_PASSWORD], errors
            )
            if refresh_token:
                return self.async_create_entry(
                    title=email,
                    data={CONF_EMAIL: email, CONF_REFRESH_TOKEN: refresh_token},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, {CONF_EMAIL: (user_input or {}).get(CONF_EMAIL)}
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            refresh_token = await self._async_sign_in(
                entry.data[CONF_EMAIL], user_input[CONF_PASSWORD], errors
            )
            if refresh_token:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_REFRESH_TOKEN: refresh_token}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )
