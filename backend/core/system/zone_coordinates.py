# backend/core/system/zone_coordinates.py
"""Where a timezone is, and which one a country is, from the tz database's own tables.

The kiosk's automatic theme turns dark between sunset and sunrise, and needs a
latitude and longitude for that — offline, with no location service. The tz
database ships one point per zone (its principal city), which is close enough
for a sunset: a few hundred kilometres moves it by minutes.

Two tables, read in order. `zone1970.tab` is the maintained one, but since 2022
it merges zones that have agreed since 1970 (Europe/Amsterdam and Europe/Oslo
now sit under Europe/Brussels and Europe/Berlin), so a zone a user can still
pick is missing from it. `zone.tab` keeps one line per country's zone and
answers for those. A name in neither may be a link — `PUT /timezone` offers
every name zoneinfo knows, America/Montreal and Asia/Istanbul among them — so
it is resolved through `tzdata.zi`'s `L <target> <link>` lines and the target
looked up instead. A zone with no point at all (`Etc/UTC`) has no location: None.

The same links answer `PUT /timezone`, which takes a legacy name a browser
reports (Chromium's `Asia/Calcutta`, a link tzdata ships only as a line of
tzdata.zi) as the zone it names (`Asia/Kolkata`).

And the kiosk, whose browser is this system and so reports nothing new, is
given the zone of the unit's WiFi country instead: `zone.tab`, which keeps one
line per zone of each country, names it when the country keeps one clock —
every zone it lists for the country on the same UTC offsets, winter and summer
(Germany's Berlin and Busingen). A country with several clocks (the United
States, Spain with the Canaries) names none: a guess there would read as a
choice and stop the next phone from handing the unit its exact zone.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiofiles

logger = logging.getLogger(__name__)

ZONE_TABLES = (
    "/usr/share/zoneinfo/zone1970.tab",
    "/usr/share/zoneinfo/zone.tab",
)
ZONE_LINKS = "/usr/share/zoneinfo/tzdata.zi"
# One line per zone of each country, the zones merged out of zone1970.tab kept.
COUNTRY_TABLE = "/usr/share/zoneinfo/zone.tab"

# ISO 6709 as the tz tables write it: ±DDMM±DDDMM or ±DDMMSS±DDDMMSS.
_ISO6709 = re.compile(
    r"^([+-])(\d{2})(\d{2})(\d{2})?([+-])(\d{3})(\d{2})(\d{2})?$"
)


def parse_iso6709(coordinates: str) -> Optional[Tuple[float, float]]:
    """`+4852+00220` → (48.8667, 2.3333). None for anything else."""
    match = _ISO6709.match(coordinates.strip())
    if not match:
        return None
    lat_sign, lat_d, lat_m, lat_s, lon_sign, lon_d, lon_m, lon_s = match.groups()

    def degrees(sign, d, m, s):
        value = int(d) + int(m) / 60 + int(s or 0) / 3600
        return round(-value if sign == "-" else value, 4)

    return degrees(lat_sign, lat_d, lat_m, lat_s), degrees(lon_sign, lon_d, lon_m, lon_s)


def find_zone_coordinates(table: str, zone: str) -> Optional[Tuple[float, float]]:
    """The coordinates one tz table gives `zone`, or None if it does not list it."""
    for line in table.splitlines():
        if line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 3 and fields[2] == zone:
            return parse_iso6709(fields[1])
    return None


def find_link_target(zi: str, zone: str) -> Optional[str]:
    """The zone a tzdata.zi link names `zone` an alias of, or None."""
    for line in zi.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[0] == "L" and fields[2] == zone:
            return fields[1]
    return None


def find_country_zones(table: str, country: str) -> List[str]:
    """The zones one tz table lists for `country`, in the table's order."""
    zones = []
    for line in table.splitlines():
        if line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 3 and country in fields[0].split(","):
            zones.append(fields[2])
    return zones


def keep_one_clock(zones: Iterable[str], year: int) -> bool:
    """Whether every zone shows the same UTC offset in January and in July of `year`.

    A name this system cannot load (a table newer than the zoneinfo tree) is
    not one clock: nothing can say it agrees.
    """
    instants = [datetime(year, month, 15, 12, tzinfo=timezone.utc) for month in (1, 7)]
    offsets = set()
    for zone in zones:
        try:
            info = ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            return False
        offsets.add(tuple(instant.astimezone(info).utcoffset() for instant in instants))
        if len(offsets) > 1:
            return False
    return len(offsets) == 1


async def _read(path: str) -> Optional[str]:
    """A missing file is skipped: the theme then stays light, which is the
    setting's safe side."""
    try:
        async with aiofiles.open(path, encoding="utf-8") as handle:
            return await handle.read()
    except OSError as exc:
        logger.warning("Cannot read tz data %s: %s", path, exc)
        return None


async def read_zone_coordinates(
    zone: Optional[str],
    tables: Optional[Iterable[str]] = None,
    links: Optional[str] = None,
) -> Optional[Tuple[float, float]]:
    """The first table that lists `zone` answers; failing that, the first that
    lists the zone it is a link to."""
    if not zone:
        return None
    contents = [table for table in [await _read(path) for path in (tables if tables is not None else ZONE_TABLES)] if table]

    def lookup(name):
        for table in contents:
            found = find_zone_coordinates(table, name)
            if found is not None:
                return found
        return None

    found = lookup(zone)
    if found is not None:
        return found
    target = await read_link_target(zone, links)
    return lookup(target) if target else None


async def read_link_target(zone: str, links: Optional[str] = None) -> Optional[str]:
    """The zone tzdata.zi makes `zone` a link to, or None (a zone, or no name)."""
    zi = await _read(links if links is not None else ZONE_LINKS)
    return find_link_target(zi, zone) if zi else None


async def read_country_zone(
    country: Optional[str],
    table: Optional[str] = None,
    year: Optional[int] = None,
) -> Optional[str]:
    """The zone `country` keeps, when it keeps one clock: the first zone.tab lists.

    None for no country (the wizard has not asked yet), a country the table does
    not list, or one whose zones keep different clocks.
    """
    if not country:
        return None
    contents = await _read(table if table is not None else COUNTRY_TABLE)
    zones = find_country_zones(contents, country) if contents else []
    if not zones:
        return None
    # Off the loop: each ZoneInfo reads a tzfile, and the United States lists 29.
    one_clock = await asyncio.to_thread(
        keep_one_clock, zones, year if year is not None else datetime.now(timezone.utc).year
    )
    if not one_clock:
        return None
    return zones[0]
