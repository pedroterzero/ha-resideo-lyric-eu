"""Offline tests for the standalone CHIL client helpers (no network, no HA).

Run:  python3 -m unittest discover -s tests   (needs aiohttp importable)
      or inside the HA container:
      docker exec -w /config/custom_components/resideo_lyric_eu home-assistant \
          python3 -m unittest discover -s tests -v
"""

from datetime import UTC, datetime, timedelta
import importlib.util
from pathlib import Path
import unittest

_spec = importlib.util.spec_from_file_location(
    "resideo_api", Path(__file__).resolve().parent.parent / "api.py"
)
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)

# Trimmed from a live T6 (EMEA, display unit Celsius, numbers in °F).
RAW = {
    "deviceID": "LCC-000000000000",
    "deviceClass": "Thermostat",
    "allowedTimeIncrements": 10,
    "scheduleStatus": "Pause",
    "thermostat": {
        "units": "Celsius",
        "minHeatSetpoint": 41.0,
        "maxHeatSetpoint": 95.0,
        "changeableValues": {
            "mode": "Heat",
            "heatSetpoint": 77.0,
            "thermostatSetpointStatus": "PermanentHold",
        },
    },
    "vacationHold": {"enabled": False},
}

SCHEDULE = {
    "deviceID": "LCC-000000000000",
    "scheduleType": "Timed",
    "scheduleSubType": "EMEA",
    "timedSchedule": {
        "days": [
            {
                "day": day,
                "periods": [
                    {
                        "periodType": "P1",
                        "startTime": "19:00:00",
                        "heatSetPoint": 55.4,
                        "coolSetPoint": 50.0,
                    }
                ]
                if day not in ("Saturday", "Sunday")
                else [],
            }
            for day in api.WEEKDAYS
        ]
    },
}


def thermostat(**changes):
    raw = {**RAW, **changes}
    return api.Thermostat(1234567, raw)


class TemperatureTest(unittest.TestCase):
    def test_read_fahrenheit_numbers_as_celsius(self):
        dev = thermostat()
        self.assertTrue(dev.celsius)
        self.assertEqual(dev.heat_setpoint, 25.0)
        self.assertEqual(dev.min_heat_setpoint, 5.0)
        self.assertEqual(dev.max_heat_setpoint, 35.0)

    def test_write_uses_native_fahrenheit_number(self):
        # 24.5 °C + unit "Celsius" is rejected by CHIL; 76.1 is accepted.
        dev = thermostat()
        self.assertEqual(dev.to_native(24.5), 76.1)
        self.assertEqual(dev.to_native(25.0), 77.0)
        self.assertEqual(dev.to_native(13.0), 55.4)

    def test_round_trip_on_half_degree_grid(self):
        dev = thermostat()
        for tenth in range(50, 351, 5):
            value = tenth / 10
            self.assertEqual(dev.temp(dev.to_native(value), dev.setpoint_step), value)


class HolidayTest(unittest.TestCase):
    NOW = datetime(2026, 10, 27, 20, 0, tzinfo=UTC)

    def test_future_booking_is_not_active(self):
        dev = thermostat(
            vacationHold={
                "enabled": True,
                "vacationStart": "2026-10-28T09:00:00",
                "vacationEnd": "2026-10-28T10:00:00",
                "heatSetpoint": 60.8,
            }
        )
        self.assertTrue(dev.vacation_enabled)
        self.assertFalse(dev.holiday_active(self.NOW))
        self.assertTrue(dev.holiday_active(self.NOW + timedelta(hours=13, minutes=30)))
        self.assertEqual(dev.vacation_heat_setpoint, 16.0)

    def test_vacation_hold_status_is_active(self):
        dev = thermostat()
        dev.raw["thermostat"] = {
            **RAW["thermostat"],
            "changeableValues": {
                **RAW["thermostat"]["changeableValues"],
                "thermostatSetpointStatus": "VacationHold",
            },
        }
        self.assertTrue(dev.holiday_active(self.NOW))

    def test_utc_strings(self):
        when = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
        self.assertEqual(api._utc_str(when), "2026-10-28T10:00:00")
        self.assertEqual(api.parse_utc("2026-10-28T10:00:00"), when)
        self.assertIsNone(api.parse_utc(None))
        with self.assertRaises(ValueError):
            api._utc_str(datetime(2026, 10, 28, 10, 0))


class ScheduleTest(unittest.TestCase):
    def test_schedule_days(self):
        days = api.schedule_days(thermostat(), SCHEDULE)
        self.assertEqual(list(days), [d.lower() for d in api.WEEKDAYS])
        self.assertEqual(days["monday"], [{"start": "19:00", "temperature": 13.0}])
        self.assertEqual(days["saturday"], [])

    def test_schedule_with_days_replaces_only_given_days(self):
        dev = thermostat()
        new = api.schedule_with_days(
            dev,
            SCHEDULE,
            {"saturday": [{"start": "08:00", "temperature": 20.0}], "monday": []},
        )
        by_day = {d["day"]: d["periods"] for d in new["timedSchedule"]["days"]}
        self.assertEqual(
            by_day["Saturday"],
            [{"startTime": "08:00:00", "heatSetPoint": 68.0, "coolSetPoint": 50.0}],
        )
        self.assertEqual(by_day["Monday"], [])
        self.assertEqual(by_day["Tuesday"], SCHEDULE["timedSchedule"]["days"][1]["periods"])
        # the input is not modified
        self.assertEqual(len(SCHEDULE["timedSchedule"]["days"][0]["periods"]), 1)


class ScheduleWriteBodyTest(unittest.IsolatedAsyncioTestCase):
    async def test_put_drops_period_type(self):
        sent = {}

        class Client(api.ResideoClient):
            def __init__(self):
                pass

            async def request(self, method, path, payload=None, params=None):
                sent.update(method=method, path=path, payload=payload)

        await Client().async_set_schedule(thermostat(), SCHEDULE)
        self.assertEqual(sent["method"], "PUT")
        self.assertEqual(sent["path"], "api/v3/locations/1234567/ThermostatSchedule")
        body = sent["payload"]
        self.assertEqual(body["deviceIDs"], ["LCC-000000000000"])
        self.assertEqual(body["scheduleSubType"], "EMEA")
        for day in body["timedSchedule"]["days"]:
            for period in day["periods"]:
                self.assertNotIn("periodType", period)


if __name__ == "__main__":
    unittest.main()
