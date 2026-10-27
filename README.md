# Resideo Lyric EU (Home Assistant custom integration)

[![CI](https://github.com/pedroterzero/ha-resideo-lyric-eu/actions/workflows/ci.yml/badge.svg)](https://github.com/pedroterzero/ha-resideo-lyric-eu/actions/workflows/ci.yml)

Controls a Honeywell Home / Resideo **T6** thermostat on the EMEA cloud
("Honeywell Home" app, CHIL backend at `lyric.alarmnet.com`) with the
same email and password you use in the app. No Resideo developer API key.

**Tested on:** one T6 (heat only, EMEA account, display unit Celsius) with
Home Assistant 2026.9. Other CHIL thermostats, Fahrenheit setups and cooling
may work but are untested. Provided as-is; issues and pull requests welcome.

## What you get

Per thermostat (one HA device):

| Entity | What it does |
|---|---|
| `climate.*` | Current/target temperature, Heat/Off, presets **schedule**, **temporary_hold**, **permanent_hold** (plus **hold_until** / **holiday** while active) |
| `switch.*_schedule` | The thermostat's own schedule: on = running, off = paused. Attributes list the weekly program |
| `binary_sensor.*_holiday` | On while a holiday is in effect; attributes `booked`, `start`, `end`, `temperature` |
| `binary_sensor.*_cloud_connection` | Thermostat online in the cloud (diagnostic) |
| `sensor.*` | Setpoint status, schedule status / period / type, heat demand, indoor and outdoor temperature, outdoor humidity |

Actions (target the climate entity):

| Action | Fields |
|---|---|
| `resideo_lyric_eu.set_hold` | `temperature` (default: current), `until` (time of day; empty = permanent) |
| `resideo_lyric_eu.set_holiday` | `start` (default now), `end`, `temperature` |
| `resideo_lyric_eu.cancel_holiday` | — |
| `resideo_lyric_eu.get_schedule` | returns `{"schedule": {"monday": [{"start": "19:00", "temperature": 13.0}], ...}}` |
| `resideo_lyric_eu.set_schedule` | `schedule`: same format; only the days given are replaced, `[]` clears a day |

Example:

```yaml
action: resideo_lyric_eu.set_schedule
target:
  entity_id: climate.badkamer_t6_cloud
data:
  schedule:
    saturday:
      - {start: "08:00", temperature: 21}
      - {start: "22:00", temperature: 16}
```

## Install

**HACS:** *HACS → ⋮ → Custom repositories*, add
`https://github.com/pedroterzero/ha-resideo-lyric-eu` as type *Integration*,
install **Resideo Lyric EU**, and restart Home Assistant.

**Manually:** copy `custom_components/resideo_lyric_eu/` into your
`config/custom_components/` and restart Home Assistant.

Then *Settings → Devices & services → Add integration → Resideo Lyric EU* and
sign in with your Honeywell Home account.

Only the email and an OAuth **refresh token** are stored, never the
password. The token is valid for 90 days and renewed on every use; if it
does expire, HA asks for the password again (reauth).

Accounts with extra sign-in steps (MFA) are not supported; accounts already
migrated to the newer "Resideo ID" sign-in are untested.

## Behaviour worth knowing

These were all observed on a real T6:

- **Paused schedule.** While the schedule is paused, the T6 turns
  "follow schedule", temporary holds and hold-until into a permanent hold
  within seconds. The integration therefore refuses those while paused;
  preset **schedule** or the Schedule switch resumes it first.
- **Pausing** keeps the setpoint of that moment as a permanent hold.
- **Temporary hold** is rewritten by the T6 to "hold until the next schedule
  period"; the climate entity still shows it as `temporary_hold`.
- **Holiday**: a future booking shows `booked: true` but the sensor turns on
  only when it starts. Cancelling a running holiday puts the T6 back on
  "follow schedule" (no hold).
- Times are on a 10-minute grid (holds, holidays, schedule periods).
- The cloud is polled every 2 minutes (schedule every 30 minutes), and again
  15 s after each change.

## API notes

- Sign-in: Azure AD B2C (`lyricprod.b2clogin.com`, policy
  `B2C_1A_SignIn_Mob_HH`) authorization code + PKCE with the app's public
  client, scripted through the B2C sign-in form. aiohttp needs
  `CookieJar(quote_cookie=False)` for that form to work.
- **Temperatures are Fahrenheit numbers even when the unit is Celsius**, on
  reads *and* writes (`{"thermostatSetpoint": 76.1, "unit": "Celsius"}` sets
  24.5 °C; sending 24.5 gives `InvalidInput`).

| Purpose | Request |
|---|---|
| State | `GET api/v2/locations` |
| Setpoint / hold | `PUT api/v2/locations/{loc}/devices/{dev}/thermostat/HeatSetpoint` |
| Mode | `PUT api/locations/{loc}/devices/{dev}/thermostat/Mode` |
| Schedule on/off | `PUT api/v2/locations/{loc}/Schedule/Status` `{"DeviceIds":[dev],"ScheduleStatus":"Resume"\|"Pause"}` |
| Schedule read | `GET api/v4/locations/{loc}/devices/{dev}/Schedule` |
| Schedule write | `PUT api/v3/locations/{loc}/ThermostatSchedule` (EMEA rejects `periodType`) |
| Holiday | `PUT api/v2/locations/{loc}/vacationHold` |

`custom_components/resideo_lyric_eu/api.py` has no Home Assistant imports and
can be used on its own.

## Tests

Offline unit tests for the conversions and request bodies (from the repo root).
CI also runs ruff, an import check against Home Assistant, hassfest and the
HACS validation:

```sh
python3 -m unittest discover -s tests -v
```

## License

MIT, see [LICENSE](LICENSE).

## Disclaimer

Unofficial, not affiliated with Resideo or Honeywell. Uses the same cloud
API as the official app, for your own devices and account; it may break when
that API changes.
