# backend/core/system/zone_coordinates.py
"""Where a timezone is, from the tz database's own tables.

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
"""
import logging
import re
from typing import Iterable, Optional, Tuple

import aiofiles

logger = logging.getLogger(__name__)

ZONE_TABLES = (
    "/usr/share/zoneinfo/zone1970.tab",
    "/usr/share/zoneinfo/zone.tab",
)
ZONE_LINKS = "/usr/share/zoneinfo/tzdata.zi"

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
    zi = await _read(links if links is not None else ZONE_LINKS)
    target = find_link_target(zi, zone) if zi else None
    return lookup(target) if target else None
