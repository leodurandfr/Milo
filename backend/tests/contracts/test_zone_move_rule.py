"""A zone's members land where the web UI's drag preview puts them.

While a zone's slider is dragged, the web UI moves its members' thumbs itself
(`moveZoneTo`, frontend/src/utils/multiroomZone.js) instead of waiting for the
echoes of its throttled sends, so it has to place each member exactly where the
backend's move will (`VolumeConfig.delta_to_average`, then `move_level`).
`zone_move_cases.json` holds both to the same answers: this file the backend,
frontend/tests/pure/multiroomZone.test.js the preview. When it fails, a
member's thumb follows the drag to one level and jumps to another when the
server's echo lands.
"""
import json
from pathlib import Path

import pytest

from backend.core.models.volume import VolumeConfig

_TABLE = json.loads((Path(__file__).resolve().parent / "zone_move_cases.json").read_text())
_CASES = _TABLE["cases"]

assert len(_CASES) >= 6 and any(
    case["moved"][m] in (case["limits"]["min_db"], case["limits"]["max_db"])
    for case in _CASES for m in case["moved"]
), "zone_move_cases.json lost its cases: a table with no room at a limit proves nothing"


@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
def test_a_zone_move_lands_where_the_preview_puts_it(case):
    config = VolumeConfig(
        limit_min_db=case["limits"]["min_db"], limit_max_db=case["limits"]["max_db"],
    )
    levels = case["levels"]

    delta = config.delta_to_average(list(levels.values()), case["target_db"])
    moved = {mac: config.move_level(level, delta) for mac, level in levels.items()}

    assert moved == pytest.approx(case["moved"])
