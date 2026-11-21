# backend/tests/test_zone_coordinates.py
"""The tz tables' ISO 6709 points, read for the kiosk's sunset-driven theme.

A wrong sign or a minutes/seconds slip puts the kiosk's sunset in another
hemisphere: the theme turns dark at noon, with nothing in any log.
"""
import pytest

from backend.core.system.zone_coordinates import (
    find_country_zones,
    find_link_target,
    keep_one_clock,
    read_country_zone,
    read_link_target,
    find_zone_coordinates,
    parse_iso6709,
    read_zone_coordinates,
)

TABLE = (
    "# tzdb zone1970.tab excerpt\n"
    "#codes\tcoordinates\tTZ\tcomments\n"
    "FR,MC\t+4852+00220\tEurope/Paris\n"
    "AQ\t-720041+0023206\tAntarctica/Troll\tTroll\n"
    "AR\t-3436-05827\tAmerica/Argentina/Buenos_Aires\tBuenos Aires (BA, CF)\n"
)


def test_degrees_and_minutes():
    assert parse_iso6709("+4852+00220") == (48.8667, 2.3333)


def test_degrees_minutes_and_seconds():
    """The seconds form, which the polar and Canadian zones use."""
    assert parse_iso6709("-720041+0023206") == (-72.0114, 2.535)


def test_southern_and_western_signs():
    assert parse_iso6709("-3436-05827") == (-34.6, -58.45)


@pytest.mark.parametrize("text", ["", "+4852", "4852+00220", "+48520+00220", "+4852+0022"])
def test_anything_else_is_no_point(text):
    assert parse_iso6709(text) is None


def test_a_listed_zone_is_found_and_comments_are_skipped():
    assert find_zone_coordinates(TABLE, "Antarctica/Troll") == (-72.0114, 2.535)
    assert find_zone_coordinates(TABLE, "America/Argentina/Buenos_Aires") == (-34.6, -58.45)


def test_utc_is_in_no_table():
    assert find_zone_coordinates(TABLE, "Etc/UTC") is None
    assert find_zone_coordinates(TABLE, "UTC") is None


async def test_a_missing_table_is_skipped_for_the_next(tmp_path):
    present = tmp_path / "zone.tab"
    present.write_text(TABLE)

    found = await read_zone_coordinates("Europe/Paris", [str(tmp_path / "absent.tab"), str(present)])

    assert found == (48.8667, 2.3333)


async def test_no_zone_reads_nothing(tmp_path):
    assert await read_zone_coordinates(None, [str(tmp_path / "absent.tab")]) is None


LINKS = (
    "# version 2026a\n"
    "Z America/Toronto -5:17:32 - LMT 1895\n"
    "L America/Toronto America/Montreal\n"
    "L Europe/Istanbul Asia/Istanbul\n"
)


def test_a_link_names_its_target():
    assert find_link_target(LINKS, "America/Montreal") == "America/Toronto"
    assert find_link_target(LINKS, "America/Toronto") is None


async def test_a_link_answers_with_its_targets_point(tmp_path):
    """`PUT /timezone` offers every name zoneinfo knows, links included, and no
    tz table lists a link: without the resolution America/Montreal would answer
    nulls and the kiosk's `auto` theme would stay light for good (29 such
    zones on this unit's tzdata)."""
    table = tmp_path / "zone1970.tab"
    table.write_text("CA\t+4339-07923\tAmerica/Toronto\tEastern - ON\n")
    links = tmp_path / "tzdata.zi"
    links.write_text(LINKS)

    found = await read_zone_coordinates("America/Montreal", [str(table)], str(links))

    assert found == (43.65, -79.3833)


async def test_a_link_is_read_from_tzdata_zi(tmp_path):
    links = tmp_path / "tzdata.zi"
    links.write_text(LINKS)

    assert await read_link_target("Asia/Istanbul", str(links)) == "Europe/Istanbul"
    assert await read_link_target("Europe/Istanbul", str(links)) is None


async def test_a_link_to_an_unlisted_zone_is_no_point(tmp_path):
    table = tmp_path / "zone1970.tab"
    table.write_text(TABLE)
    links = tmp_path / "tzdata.zi"
    links.write_text(LINKS)

    assert await read_zone_coordinates("Asia/Istanbul", [str(table)], str(links)) is None


# zone.tab as it lists three countries: one zone, two that keep one clock, two
# that do not. Real zone names, because the clock is read from this system's
# zoneinfo, never from the table.
COUNTRIES = (
    "# tzdb zone.tab excerpt\n"
    "#code\tcoordinates\tTZ\tcomments\n"
    "DE\t+5230+01322\tEurope/Berlin\tmost of Germany\n"
    "DE\t+4742+00841\tEurope/Busingen\tBusingen\n"
    "ES\t+4024-00341\tEurope/Madrid\tSpain (mainland)\n"
    "ES\t+3553-00519\tAfrica/Ceuta\tCeuta, Melilla\n"
    "ES\t+2806-01524\tAtlantic/Canary\tCanary Islands\n"
    "SG\t+0117+10351\tAsia/Singapore\n"
)


@pytest.mark.parametrize(
    "country,zone",
    [("SG", "Asia/Singapore"), ("DE", "Europe/Berlin"), ("ES", None), ("MA", None), ("", None)],
)
async def test_a_country_names_its_zone_only_when_it_keeps_one_clock(tmp_path, country, zone):
    """The kiosk's browser is this system, so the unit's WiFi country is the only
    hint of where it is. Germany's two zones keep one clock and Berlin is the
    one to show; Spain's Canaries are an hour behind the mainland, so a pick
    there is a guess, and a guess would read as a choice — no phone opening the
    UI later could then hand the unit its exact zone."""
    table = tmp_path / "zone.tab"
    table.write_text(COUNTRIES)

    assert await read_country_zone(country, str(table), year=2026) == zone


def test_the_country_column_may_name_several_countries():
    """zone1970.tab shares a line between countries (`FR,MC`): a reader that
    compared the whole column would find no zone for Monaco."""
    assert find_country_zones(TABLE, "MC") == ["Europe/Paris"]
    assert find_country_zones(TABLE, "AR") == ["America/Argentina/Buenos_Aires"]


def test_a_zone_this_system_cannot_load_is_not_one_clock():
    assert keep_one_clock(["Europe/Paris", "Middle/Earth"], 2026) is False
    assert keep_one_clock(["Europe/Paris", "Europe/Berlin"], 2026) is True


async def test_no_country_table_names_no_zone(tmp_path):
    assert await read_country_zone("SG", str(tmp_path / "absent.tab"), year=2026) is None


async def test_the_unit_s_own_zone_tab_names_the_wizard_s_countries():
    """Against this host's tzdata, which is the unit's: the countries named in
    the WiFi list adopt the zone a phone there would report, and the United
    States, which keeps six clocks, adopts none. Asserts the table was read at
    all first: a missing zone.tab would pass the None and fail nothing else."""
    found = {c: await read_country_zone(c) for c in ("FR", "IN", "SG", "MA", "GB", "US")}

    assert found["FR"] == "Europe/Paris"
    assert found == {
        "FR": "Europe/Paris",
        "IN": "Asia/Kolkata",
        "SG": "Asia/Singapore",
        "MA": "Africa/Casablanca",
        "GB": "Europe/London",
        "US": None,
    }
