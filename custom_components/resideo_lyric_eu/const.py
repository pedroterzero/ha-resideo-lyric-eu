"""Constants for Resideo Lyric EU."""

from datetime import timedelta

DOMAIN = "resideo_lyric_eu"

CONF_REFRESH_TOKEN = "refresh_token"

SCAN_INTERVAL = timedelta(seconds=120)
SCHEDULE_INTERVAL = timedelta(minutes=30)
# The device sometimes corrects a write a few seconds later; look again.
FOLLOWUP_REFRESH = 15

SERVICE_SET_HOLD = "set_hold"
SERVICE_SET_HOLIDAY = "set_holiday"
SERVICE_CANCEL_HOLIDAY = "cancel_holiday"
SERVICE_GET_SCHEDULE = "get_schedule"
SERVICE_SET_SCHEDULE = "set_schedule"
