"""Async client for the Honeywell Home EMEA (Lyric / CHIL) consumer API.

Auth: Azure AD B2C auth-code + PKCE with the EMEA app's own public client,
headless via the B2C SelfAsserted form. API: CHIL (https://lyric.alarmnet.com/).

This module has no Home Assistant imports so it can be exercised standalone.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import formatdate
import hashlib
import json
import logging
import re
import secrets
import time
from typing import Any
import urllib.parse
import uuid

import aiohttp

_LOGGER = logging.getLogger(__name__)

B2C_HOST = "https://lyricprod.b2clogin.com"
TENANT = "lyricprod.onmicrosoft.com"
POLICY = "B2C_1A_SignIn_Mob_HH"
CLIENT_ID = "d7baddb4-d9f3-4575-af28-f1b04a7883f2"
REDIRECT_URI = "com.honeywell.acs.lyric.enterprise://oauth2redirect"
SCOPE = (
    "https://lyricprod.onmicrosoft.com/CHILAPIService/user_impersonation "
    "profile email openid offline_access"
)
AUTHORIZE_URL = f"{B2C_HOST}/{TENANT}/{POLICY}/oauth2/v2.0/authorize"
TOKEN_URL = f"{B2C_HOST}/{TENANT}/{POLICY}/oauth2/v2.0/token"

CHIL_BASE = "https://lyric.alarmnet.com/"
APIM_KEY = "8c9485374bab4b1aa4b3555ac2f032c4"
APP_VERSION = "6.21.1"
USER_AGENT = "Mozilla/5.0 (Linux; Android 14) HoneywellHome/6.21.1"

TIMEOUT = aiohttp.ClientTimeout(total=30)
# Refresh the access token this many seconds before it expires.
TOKEN_SKEW = 120


class ResideoError(Exception):
    """Base error."""


class ResideoAuthError(ResideoError):
    """Credentials or refresh token rejected; user must re-authenticate."""


class ResideoConnectionError(ResideoError):
    """Network / server trouble; retry later."""


class ResideoApiError(ResideoError):
    """CHIL returned an unexpected status."""

    def __init__(self, status: int, text: str) -> None:
        super().__init__(f"HTTP {status}: {text[:300]}")
        self.status = status


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def _parse_settings(html: str) -> dict[str, str]:
    """Pull the SETTINGS blob B2C renders into its sign-in page."""
    m = re.search(r"var SETTINGS\s*=\s*(\{.*?\});", html, re.S)
    if not m:
        raise ResideoError("B2C sign-in page layout not recognised (no SETTINGS)")
    s = json.loads(m.group(1))
    return {
        "transId": s["transId"],
        "csrf": s["csrf"],
        "api": s["api"],
        "tenant_path": s["hosts"]["tenant"].rstrip("/"),
        "policy": s.get("hosts", {}).get("policy") or POLICY,
    }


async def _token_request(
    session: aiohttp.ClientSession, data: dict[str, str]
) -> dict[str, Any]:
    try:
        async with session.post(
            TOKEN_URL,
            data={"client_id": CLIENT_ID, "scope": SCOPE, **data},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        ) as resp:
            text = await resp.text()
            status = resp.status
    except (aiohttp.ClientError, TimeoutError) as err:
        raise ResideoConnectionError(f"token endpoint: {err}") from err
    try:
        body = json.loads(text)
    except ValueError:
        body = {}
    if status == 200 and "access_token" in body:
        return body
    if 400 <= status < 500:
        # invalid_grant = refresh token expired/revoked -> reauth
        raise ResideoAuthError(
            f"token endpoint {status}: {body.get('error')} "
            f"{(body.get('error_description') or '')[:200]}"
        )
    raise ResideoConnectionError(f"token endpoint {status}: {text[:200]}")


def login_cookie_jar() -> aiohttp.CookieJar:
    """Cookie jar suitable for `async_login`."""
    return aiohttp.CookieJar(quote_cookie=False)


async def async_login(
    session: aiohttp.ClientSession, email: str, password: str
) -> dict[str, Any]:
    """Run the headless B2C sign-in and return the token response.

    `session` needs its own cookie jar (B2C keeps transaction state in cookies)
    created with quote_cookie=False: B2C rejects the quoted form aiohttp would
    otherwise send for its base64 cookie values. See `login_cookie_jar()`.
    The password is used for the single SelfAsserted POST and not retained.
    """
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(12)
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "prompt": "login",
    }
    headers = {"User-Agent": USER_AGENT}
    try:
        async with session.get(
            AUTHORIZE_URL, params=params, headers=headers, timeout=TIMEOUT
        ) as resp:
            if resp.status != 200:
                raise ResideoConnectionError(f"authorize page HTTP {resp.status}")
            cfg = _parse_settings(await resp.text())

        base = B2C_HOST + cfg["tenant_path"]
        async with session.post(
            f"{base}/SelfAsserted",
            params={"tx": cfg["transId"], "p": cfg["policy"]},
            headers={
                **headers,
                "X-CSRF-TOKEN": cfg["csrf"],
                "X-Requested-With": "XMLHttpRequest",
            },
            data={
                "request_type": "RESPONSE",
                "signInName": email,
                "password": password,
            },
            timeout=TIMEOUT,
        ) as resp:
            text = await resp.text()
        try:
            body = json.loads(text)
        except ValueError:
            body = {}
        if str(body.get("status")) != "200":
            raise ResideoAuthError(
                f"sign-in rejected: {body.get('message') or text[:200]}"
            )

        async with session.get(
            f"{base}/api/{cfg['api']}/confirmed",
            params={
                "rememberMe": "false",
                "csrf_token": cfg["csrf"],
                "tx": cfg["transId"],
                "p": cfg["policy"],
            },
            headers=headers,
            allow_redirects=False,
            timeout=TIMEOUT,
        ) as resp:
            location = resp.headers.get("Location", "")
            status = resp.status
    except (aiohttp.ClientError, TimeoutError) as err:
        raise ResideoConnectionError(f"B2C sign-in: {err}") from err

    if not location.startswith(REDIRECT_URI):
        raise ResideoAuthError(
            f"no auth code after sign-in (HTTP {status}); "
            "account may need MFA or an extra step in the app"
        )
    query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
    if "error" in query:
        raise ResideoAuthError(
            f"B2C error {query.get('error')}: {query.get('error_description')}"
        )
    if query.get("state", [None])[0] != state or "code" not in query:
        raise ResideoAuthError("B2C redirect missing code or state mismatch")

    return await _token_request(
        session,
        {
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
    )


class ResideoClient:
    """CHIL API client with automatic token refresh.

    `on_refresh_token` is awaited with the new refresh token whenever B2C
    rotates it, so the caller can persist it.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        refresh_token: str,
        on_refresh_token: Callable[[str], Awaitable[None] | None] | None = None,
        access_token: str | None = None,
        expires_at: float = 0,
    ) -> None:
        self._session = session
        self._refresh_token = refresh_token
        self._on_refresh_token = on_refresh_token
        self._access_token = access_token
        self._expires_at = expires_at
        self._lock = asyncio.Lock()

    @classmethod
    def from_token_response(
        cls,
        session: aiohttp.ClientSession,
        tokens: dict[str, Any],
        on_refresh_token: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> ResideoClient:
        return cls(
            session,
            tokens["refresh_token"],
            on_refresh_token,
            tokens["access_token"],
            time.monotonic() + int(tokens.get("expires_in", 900)),
        )

    @property
    def refresh_token(self) -> str:
        return self._refresh_token

    async def _async_refresh(self) -> None:
        tokens = await _token_request(
            self._session,
            {"grant_type": "refresh_token", "refresh_token": self._refresh_token},
        )
        self._access_token = tokens["access_token"]
        self._expires_at = time.monotonic() + int(tokens.get("expires_in", 900))
        new_refresh = tokens.get("refresh_token")
        if new_refresh and new_refresh != self._refresh_token:
            self._refresh_token = new_refresh
            if self._on_refresh_token:
                res = self._on_refresh_token(new_refresh)
                if asyncio.iscoroutine(res):
                    await res
        _LOGGER.debug("access token refreshed")

    async def _async_access_token(self, force: bool = False) -> str:
        async with self._lock:
            if (
                force
                or not self._access_token
                or time.monotonic() > self._expires_at - TOKEN_SKEW
            ):
                await self._async_refresh()
            assert self._access_token
            return self._access_token

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Ocp-Apim-Subscription-Key": APIM_KEY,
            "ActivityId": str(uuid.uuid4()),
            "User-Agent": USER_AGENT,
            "MobileClientTime": formatdate(usegmt=True),
            "AppVer": APP_VERSION,
        }

    async def request(
        self, method: str, path: str, payload: Any = None, params: dict | None = None
    ) -> Any:
        """Call CHIL; retries once with a fresh token on 401."""
        for attempt in (0, 1):
            token = await self._async_access_token(force=attempt == 1)
            try:
                async with self._session.request(
                    method,
                    CHIL_BASE + path.lstrip("/"),
                    headers=self._headers(token),
                    json=payload,
                    params=params,
                    timeout=TIMEOUT,
                ) as resp:
                    text = await resp.text()
                    status = resp.status
            except (aiohttp.ClientError, TimeoutError) as err:
                raise ResideoConnectionError(f"{method} {path}: {err}") from err
            if status == 401 and attempt == 0:
                _LOGGER.debug("%s %s -> 401, retrying with fresh token", method, path)
                continue
            if status == 401:
                raise ResideoAuthError(f"{method} {path}: 401 after token refresh")
            if status >= 500:
                raise ResideoConnectionError(f"{method} {path}: HTTP {status} {text[:300]}")
            if status >= 400:
                raise ResideoApiError(status, text)
            if not text:
                return None
            try:
                return json.loads(text)
            except ValueError:
                return text
        raise ResideoError("unreachable")

    async def async_get_session(self) -> Any:
        return await self.request("GET", "api/v2/session")

    async def async_get_locations(self) -> list[dict[str, Any]]:
        data = await self.request("GET", "api/v2/locations")
        if isinstance(data, dict):
            data = data.get("locations", [data])
        return data or []

    # --- writes (bodies mirror the app: SetpointWithStatus, Mode, vacationHold) ---

    async def async_set_heat_setpoint(
        self,
        dev: Thermostat,
        status: str,
        setpoint: float | None = None,
        hold_until: str | None = None,
    ) -> Any:
        """PUT a heat setpoint with hold status.

        status: NoHold (resume schedule; no setpoint), TemporaryHold (until the
        next schedule period), HoldUntil (hold_until "HH:MM" local), PermanentHold.
        `setpoint` is in the display unit. Like reads, CHIL wants the number in
        its native unit (°F) with `unit` naming the display unit; a °C number
        is rejected with InvalidInput.
        """
        body: dict[str, Any] = {"thermostatSetpointStatus": status}
        if status != "NoHold":
            if setpoint is None:
                raise ValueError(f"{status} needs a setpoint")
            body["thermostatSetpoint"] = dev.to_native(setpoint)
            body["unit"] = dev.unit
        if status == "HoldUntil":
            if not hold_until:
                raise ValueError("HoldUntil needs hold_until")
            body["holdUntil"] = hold_until
        return await self.request(
            "PUT",
            f"api/v2/locations/{dev.location_id}/devices/{dev.device_id}"
            "/thermostat/HeatSetpoint",
            body,
        )

    async def async_set_mode(self, dev: Thermostat, mode: str) -> Any:
        """PUT system mode, one of dev.allowed_modes (e.g. Heat, Off)."""
        return await self.request(
            "PUT",
            f"api/locations/{dev.location_id}/devices/{dev.device_id}/thermostat/Mode",
            {"thermostatMode": mode},
        )

    async def async_set_schedule_enabled(self, dev: Thermostat, enabled: bool) -> Any:
        """Resume (Pause -> Run) or pause the device's own schedule."""
        return await self.request(
            "PUT",
            f"api/v2/locations/{dev.location_id}/Schedule/Status",
            {
                "DeviceIds": [dev.device_id],
                "ScheduleStatus": "Resume" if enabled else "Pause",
            },
        )

    async def async_get_schedule(self, dev: Thermostat) -> dict[str, Any]:
        """Timed schedule: {scheduleType, scheduleSubType, timedSchedule:{days:[
        {day, periods:[{periodType, startTime "HH:MM:SS", heatSetPoint (native)}]}]}}."""
        return await self.request(
            "GET",
            f"api/v4/locations/{dev.location_id}/devices/{dev.device_id}/Schedule",
        )

    async def async_set_schedule(self, dev: Thermostat, schedule: dict[str, Any]) -> Any:
        """PUT a full timed schedule, same shape as async_get_schedule returns.

        EMEA schedules reject periodType/isCancelled (HTTP 400), though GET
        returns periodType, so those are dropped.
        """
        days = [
            {
                "day": day["day"],
                "periods": [
                    {k: v for k, v in period.items() if k not in ("periodType", "isCancelled")}
                    for period in day.get("periods", [])
                ],
            }
            for day in schedule["timedSchedule"]["days"]
        ]
        return await self.request(
            "PUT",
            f"api/v3/locations/{dev.location_id}/ThermostatSchedule",
            {
                "deviceIDs": [dev.device_id],
                "scheduleType": schedule["scheduleType"],
                "scheduleSubType": schedule.get("scheduleSubType"),
                "timedSchedule": {"days": days},
            },
        )

    async def async_set_holiday(
        self, dev: Thermostat, start: datetime, end: datetime, heat_setpoint: float
    ) -> Any:
        """Enable holiday/away from start to end (aware datetimes).

        heat_setpoint is in the display unit.
        """
        return await self.request(
            "PUT",
            f"api/v2/locations/{dev.location_id}/vacationHold",
            {
                "VacationStart": _utc_str(start),
                "VacationEnd": _utc_str(end),
                "thermostatVacationHoldSettings": [
                    {
                        "deviceID": dev.device_id,
                        "heatSetpoint": dev.to_native(heat_setpoint),
                        "units": dev.unit,
                    }
                ],
                "Enabled": True,
            },
        )

    async def async_cancel_holiday(self, dev: Thermostat) -> Any:
        return await self.request(
            "PUT",
            f"api/v2/locations/{dev.location_id}/vacationHold",
            {
                "thermostatVacationHoldSettings": [{"deviceID": dev.device_id}],
                "Enabled": False,
            },
        )


def _utc_str(value: datetime) -> str:
    """CHIL vacation timestamps: UTC, no zone suffix."""
    if value.tzinfo is None:
        raise ValueError("timezone-aware datetime required")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def parse_utc(value: str | None) -> datetime | None:
    """Parse a CHIL UTC timestamp (no zone suffix) to an aware datetime."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.rstrip("Z"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _f_to_c(value: float) -> float:
    return (value - 32) * 5 / 9


def _c_to_f(value: float) -> float:
    return value * 9 / 5 + 32


class Thermostat:
    """View over one CHIL thermostat device dict.

    CHIL reports temperature numbers in Fahrenheit even when the account's
    display unit (`thermostat.units`) is Celsius. Values exposed here are
    converted to the display unit.
    """

    def __init__(self, location_id: int, raw: dict[str, Any]) -> None:
        self.location_id = location_id
        self.raw = raw

    @property
    def device_id(self) -> str:
        return self.raw["deviceID"]

    @property
    def name(self) -> str:
        return self.raw.get("userDefinedDeviceName") or self.raw.get("name") or self.device_id

    @property
    def _tstat(self) -> dict[str, Any]:
        return self.raw.get("thermostat") or {}

    @property
    def changeable(self) -> dict[str, Any]:
        return self._tstat.get("changeableValues") or {}

    @property
    def unit(self) -> str:
        """Display unit, 'Celsius' or 'Fahrenheit' (also the write `unit`)."""
        return "Fahrenheit" if self._tstat.get("units") == "Fahrenheit" else "Celsius"

    @property
    def celsius(self) -> bool:
        return self.unit == "Celsius"

    @property
    def _native_fahrenheit(self) -> bool:
        # No Celsius setpoint range reaches 50; the T6 reports 41..95.
        limit = self._tstat.get("maxHeatSetpoint") or self._tstat.get("maxCoolSetpoint")
        return limit is None or limit > 50

    def temp(self, value: float | None, step: float = 0.1) -> float | None:
        """Convert a raw CHIL temperature to the display unit, rounded to `step`."""
        if value is None:
            return None
        if self._native_fahrenheit and self.celsius:
            value = _f_to_c(value)
        elif not self._native_fahrenheit and not self.celsius:
            value = _c_to_f(value)
        return round(round(value / step) * step, 1)

    def to_native(self, value: float) -> float:
        """Display-unit temperature to the number CHIL expects on writes."""
        if self._native_fahrenheit and self.celsius:
            value = _c_to_f(value)
        elif not self._native_fahrenheit and not self.celsius:
            value = _f_to_c(value)
        return round(value, 1)

    @property
    def setpoint_step(self) -> float:
        return 0.5 if self.celsius else 1.0

    @property
    def indoor_temperature(self) -> float | None:
        return self.temp(self._tstat.get("indoorTemperature"))

    @property
    def outdoor_temperature(self) -> float | None:
        return self.temp(self._tstat.get("outdoorTemperature"))

    @property
    def outdoor_humidity(self) -> float | None:
        return self.raw.get("displayedOutdoorHumidity")

    def _setpoint(self, key: str) -> float | None:
        # Right after a NoHold write the cloud reports 0 until the device syncs.
        raw = self.changeable.get(key)
        return self.temp(raw, self.setpoint_step) if raw else None

    @property
    def heat_setpoint(self) -> float | None:
        return self._setpoint("heatSetpoint")

    @property
    def cool_setpoint(self) -> float | None:
        return self._setpoint("coolSetpoint")

    @property
    def min_heat_setpoint(self) -> float | None:
        return self.temp(self._tstat.get("minHeatSetpoint"), self.setpoint_step)

    @property
    def max_heat_setpoint(self) -> float | None:
        return self.temp(self._tstat.get("maxHeatSetpoint"), self.setpoint_step)

    @property
    def mode(self) -> str | None:
        return self.changeable.get("mode")

    @property
    def allowed_modes(self) -> list[str]:
        return self._tstat.get("allowedModes") or []

    @property
    def operation_mode(self) -> str | None:
        return (self._tstat.get("operationStatus") or {}).get("mode")

    @property
    def setpoint_status(self) -> str | None:
        return self.changeable.get("thermostatSetpointStatus")

    @property
    def schedule_status(self) -> str | None:
        return self.raw.get("scheduleStatus")

    @property
    def schedule_paused(self) -> bool:
        """Schedule paused on the device (scheduleStatus Pause; running reads Resume)."""
        return self.schedule_status == "Pause"

    @property
    def schedule_type(self) -> str | None:
        st = self.raw.get("scheduleType") or {}
        parts = [p for p in (st.get("scheduleType"), st.get("scheduleSubType")) if p]
        return " ".join(parts) or None

    @property
    def schedule_period(self) -> dict[str, Any]:
        return self.raw.get("currentSchedulePeriod") or {}

    @property
    def demand(self) -> dict[str, Any]:
        return self.raw.get("heatAndCoolDemand") or {}

    @property
    def vacation(self) -> dict[str, Any]:
        return self.raw.get("vacationHold") or {}

    @property
    def vacation_enabled(self) -> bool:
        """A holiday is booked (it may start in the future)."""
        return bool(self.vacation.get("enabled"))

    @property
    def vacation_start(self) -> datetime | None:
        return parse_utc(self.vacation.get("vacationStart"))

    @property
    def vacation_end(self) -> datetime | None:
        return parse_utc(self.vacation.get("vacationEnd"))

    @property
    def vacation_heat_setpoint(self) -> float | None:
        raw = self.vacation.get("heatSetpoint")
        return self.temp(raw, self.setpoint_step) if raw else None

    def holiday_active(self, now: datetime) -> bool:
        """Holiday currently in effect."""
        if self.setpoint_status == "VacationHold":
            return True
        if not self.vacation_enabled:
            return False
        start, end = self.vacation_start, self.vacation_end
        return (start is None or start <= now) and (end is None or now < end)

    @property
    def is_alive(self) -> bool:
        return bool(self.raw.get("isAlive"))

    @property
    def time_increment(self) -> int:
        """Minutes granularity for hold/holiday times."""
        return int(self.raw.get("allowedTimeIncrements") or 10)


def thermostats(locations: list[dict[str, Any]]) -> dict[str, Thermostat]:
    """All thermostats across locations, keyed by deviceID."""
    out: dict[str, Thermostat] = {}
    for loc in locations:
        for dev in loc.get("devices") or []:
            if dev.get("deviceClass") == "Thermostat" and dev.get("deviceID"):
                out[dev["deviceID"]] = Thermostat(loc["locationID"], dev)
    return out


WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def schedule_days(dev: Thermostat, schedule: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Timed schedule as {"monday": [{"start": "HH:MM", "temperature": °}], ...}."""
    by_day = {d.get("day"): d.get("periods") or [] for d in schedule["timedSchedule"]["days"]}
    return {
        day.lower(): [
            {
                "start": (p.get("startTime") or "")[:5],
                "temperature": dev.temp(p.get("heatSetPoint"), dev.setpoint_step),
            }
            for p in by_day.get(day, [])
        ]
        for day in WEEKDAYS
    }


def schedule_with_days(
    dev: Thermostat, schedule: dict[str, Any], days: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    """Copy of `schedule` with the given days (schedule_days format) replaced."""
    # GET returns a coolSetPoint on every period (heat-only T6: a fixed
    # 50 °F); keep sending the existing value.
    cool = next(
        (
            p["coolSetPoint"]
            for d in schedule["timedSchedule"]["days"]
            for p in d.get("periods") or []
            if p.get("coolSetPoint") is not None
        ),
        None,
    )
    out_days = []
    for d in schedule["timedSchedule"]["days"]:
        new = days.get(str(d.get("day")).lower())
        if new is None:
            out_days.append(d)
            continue
        out_days.append(
            {
                "day": d["day"],
                "periods": [
                    {
                        "startTime": f"{p['start']}:00",
                        "heatSetPoint": dev.to_native(p["temperature"]),
                        **({"coolSetPoint": cool} if cool is not None else {}),
                    }
                    for p in new
                ],
            }
        )
    return {**schedule, "timedSchedule": {**schedule["timedSchedule"], "days": out_days}}
