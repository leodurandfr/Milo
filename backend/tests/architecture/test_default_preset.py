# backend/tests/architecture/test_default_preset.py
"""Pin the "Default" preset to what a freshly flashed unit actually runs.

The preset means one thing to the person pressing it: *put this back as it came
out of the box*. That promise is only true while it matches what
`provisioning/snapcast.sh` writes into snapserver.conf at image build.

Nothing else can enforce it. The provisioning tree is shell, built inside a
cloned pi-gen checkout that cannot reach this repo, so the two declarations can
only agree by being checked here. They already disagreed once, invisibly: the
shipped values matched none of the three presets that existed, so a unit nobody
had configured showed no selected preset at all.
"""
import re
from pathlib import Path

import pytest

from backend.core.multiroom.snapcast import NETWORK_PRESETS

PROVISIONING = Path(__file__).resolve().parents[3] / "provisioning" / "snapcast.sh"

# Left is the conf key the shell writes, right is the name the API exposes.
CONF_TO_API = {"buffer": "buffer_ms", "codec": "codec", "chunk_ms": "chunk_ms"}


def _shipped_stream_values():
    """The `[stream]` assignments `provisioning/snapcast.sh` writes.

    Reads the heredoc literally rather than running it: the script builds an
    image, and a test that executed it would be testing the shell.
    """
    source = PROVISIONING.read_text(encoding="utf-8")
    stream = source.split("[stream]", 1)[1].split("[http]", 1)[0]

    values = {}
    for key in CONF_TO_API:
        match = re.search(rf"^{key}\s*=\s*(\S+)\s*$", stream, re.MULTILINE)
        if match:
            values[key] = match.group(1)
    return values


def test_the_extractor_reads_the_provisioning_script_at_all():
    """A parse that silently found nothing would make every check below pass on
    an empty dict -- the exact shape of a guardrail that cannot fail."""
    assert PROVISIONING.exists(), f"{PROVISIONING} is the source of the shipped config"

    values = _shipped_stream_values()

    assert set(values) == set(CONF_TO_API), f"only parsed {sorted(values)}"


def test_exactly_one_preset_ships():
    """The three named presets were replaced by the automatic analysis. A second
    entry here means someone added a canned opinion back without deciding what
    it is for -- which is how `responsive`/`balanced`/`robust` came to be three
    words no user could rank."""
    assert [preset["id"] for preset in NETWORK_PRESETS] == ["default"]


@pytest.mark.parametrize("conf_key,api_key", sorted(CONF_TO_API.items()))
def test_the_default_preset_matches_the_flashed_image(conf_key, api_key):
    """Selecting "Default" must restore the factory configuration exactly.

    A preset that drifts from the image is worse than no preset: it presents
    itself as the known-good starting point while being a fourth set of values
    nobody measured.
    """
    shipped = _shipped_stream_values()[conf_key]
    offered = NETWORK_PRESETS[0]["config"][api_key]

    assert str(offered) == shipped
