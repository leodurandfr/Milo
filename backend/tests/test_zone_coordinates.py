# backend/tests/test_zone_coordinates.py
"""The tz tables' ISO 6709 points, read for the kiosk's sunset-driven theme.

A wrong sign or a minutes/seconds slip puts the kiosk's sunset in another
hemisphere: the theme turns dark at noon, with nothing in any log.
"""
import pytest

from backend.core.system.zone_coordinates import (
    find_link_target,
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


async def test_a_link_to_an_unlisted_zone_is_no_point(tmp_path):
    table = tmp_path / "zone1970.tab"
    table.write_text(TABLE)
    links = tmp_path / "tzdata.zi"
    links.write_text(LINKS)

    assert await read_zone_coordinates("Asia/Istanbul", [str(table)], str(links)) is None
