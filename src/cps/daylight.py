"""
Where the sun is for a student (DECISIONS.md D76): the sky behind a plan's pages
follows the real day where the plan lives, sunrise and sunset moving with the
seasons, instead of four fixed parts of the clock.

The place is the plan's time zone: the tz database gives each zone the coordinates
of its main city (zone1970.tab, shipped by the `tzdata` package), which is close
enough for a colour; a zone with none falls back to its UTC offset (15 degrees of
longitude an hour) at latitude 45. The sun's elevation comes from NOAA's general
solar position equations (fractional year, equation of time, declination), good to
a fraction of a degree, which no one can see in a sky drawn in soft colours.

`day_position` folds the elevation into one number from 0 to 4 that the sky's
colours are drawn along: 0 deep night, 1 sunrise, 2 the sun at its highest, 3
sunset, back to night at 4. Daytime is measured against that day's highest sun, so
a winter noon is "noon" too.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta
from functools import cache
from importlib import resources
from zoneinfo import ZoneInfo

TWILIGHT = 18.0  # degrees below the horizon where night is complete (astronomical twilight)
_PLACE = re.compile(r"^([+-])(\d{2})(\d{2})(\d{2})?([+-])(\d{3})(\d{2})(\d{2})?$")


def _degrees(sign: str, whole: str, minutes: str, seconds: str | None) -> float:
    value = int(whole) + int(minutes) / 60 + int(seconds or 0) / 3600
    return -value if sign == "-" else value


@cache
def _places() -> dict[str, tuple[float, float]]:
    """Every zone's main city, from the tz database's own tables."""
    found: dict[str, tuple[float, float]] = {}
    for table in ("zone.tab", "zone1970.tab"):  # the second wins: it is the maintained one
        try:
            text = resources.files("tzdata").joinpath("zoneinfo", table).read_text(encoding="utf-8")
        except (FileNotFoundError, ModuleNotFoundError):
            continue
        for line in text.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split("\t")
            match = _PLACE.match(parts[1]) if len(parts) > 2 else None
            if match:
                g = match.groups()
                found[parts[2]] = (_degrees(g[0], g[1], g[2], g[3]), _degrees(g[4], g[5], g[6], g[7]))
    return found


def place(tz: str, when: datetime | None = None) -> tuple[float, float]:
    """Latitude and longitude for a time zone (east and north positive)."""
    known = _places().get(tz)
    if known:
        return known
    offset = (when or datetime.now(UTC)).astimezone(ZoneInfo(tz)).utcoffset() or timedelta(0)
    return 45.0, offset.total_seconds() / 3600 * 15


def _sun(when: datetime) -> tuple[float, float]:
    """Declination (radians) and the equation of time (minutes) at an instant."""
    utc = when.astimezone(UTC)
    hour = utc.hour + utc.minute / 60 + utc.second / 3600
    g = 2 * math.pi / 365 * (utc.timetuple().tm_yday - 1 + (hour - 12) / 24)
    equation = 229.18 * (
        0.000075
        + 0.001868 * math.cos(g)
        - 0.032077 * math.sin(g)
        - 0.014615 * math.cos(2 * g)
        - 0.040849 * math.sin(2 * g)
    )
    declination = (
        0.006918
        - 0.399912 * math.cos(g)
        + 0.070257 * math.sin(g)
        - 0.006758 * math.cos(2 * g)
        + 0.000907 * math.sin(2 * g)
        - 0.002697 * math.cos(3 * g)
        + 0.00148 * math.sin(3 * g)
    )
    return declination, equation


def elevation(latitude: float, longitude: float, when: datetime) -> float:
    """The sun's height above the horizon in degrees (negative below it)."""
    declination, equation = _sun(when)
    utc = when.astimezone(UTC)
    solar_minutes = utc.hour * 60 + utc.minute + utc.second / 60 + equation + 4 * longitude
    hour_angle = math.radians(solar_minutes / 4 - 180)
    lat = math.radians(latitude)
    cosine = math.sin(lat) * math.sin(declination)
    cosine += math.cos(lat) * math.cos(declination) * math.cos(hour_angle)
    return 90 - math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def highest(latitude: float, when: datetime) -> float:
    """The sun's height at that day's solar noon, in degrees."""
    declination, _ = _sun(when)
    return 90 - abs(latitude - math.degrees(declination))


def day_position(latitude: float, longitude: float, when: datetime) -> float:
    """Where the day is, from 0 to 4: 0 deep night, up to 1 at sunrise through the
    dawn's twilight, 2 at the highest sun, 3 at sunset, back towards 4 (the same as 0)
    through dusk."""
    now = elevation(latitude, longitude, when)
    rising = elevation(latitude, longitude, when + timedelta(minutes=5)) > now
    if now >= 0:
        share = min(1.0, now / max(1.0, highest(latitude, when)))
        return 1 + share if rising else 3 - share
    if now > -TWILIGHT:
        share = (TWILIGHT + now) / TWILIGHT
        return share if rising else 4 - share
    return 0.0
