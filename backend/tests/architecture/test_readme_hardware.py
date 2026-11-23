"""Structural guardrail: the README's boards are the boards the setup wizard offers.

README.md § Hardware is the first thing a buyer reads before ordering a board,
and it names them by model ("Amp4 Pro", "DAC2 HD") rather than linking the
generated table. That list is a restatement of `hardware/registry.py::AUDIO_CARDS`,
the one table the wizard's audio step and the satellite's card picker read, and
a restatement is what drifts: a card added to the registry and left out of the
README is a board nobody knows they can buy; a board the README names that the
registry no longer lists is one that, once bought and seated, cannot be picked —
the unit then does not set up at all, since Milō plays through a HAT or not at all.

So both directions are held here: every model a registry label names appears in
the section, and every board model the section names is one a label names. The
comparison is a function of the text, so the drift test feeds it a copy with one
board taken out and one added, and watches each go red.
"""
import re
from pathlib import Path

from backend.hardware.registry import AUDIO_CARDS

REPO_ROOT = Path(__file__).resolve().parents[3]
README = REPO_ROOT / "README.md"

# A board model as prose writes it: an Amp with its number, or a DAC2 / DAC+,
# with the suffixes HiFiBerry's names carry. "DAC" alone is the generic word
# ("a DAC for an amplifier", "a USB DAC"), never a model.
_MODEL = re.compile(r"(?<![\w+])(?:Amp\d+|DAC2|DAC\+)(?: ADC)?(?: Pro| HD)?(?![\w+])")


def _registry_models() -> set[str]:
    """Every model the registry's labels name: "HiFiBerry DAC2 Pro / DAC+ Pro"
    names two, the same board across two generations."""
    models = set()
    for card_id, card in AUDIO_CARDS.items():
        if card["brand"] is None:  # "none": a satellite not configured yet, never offered
            continue
        assert card["label"].startswith(card["brand"] + " "), card_id
        for name in card["label"][len(card["brand"]) + 1:].split(" / "):
            models.add(name.strip())
    return models


def _hardware_section(text: str) -> str:
    match = re.search(r"^## Hardware\n(.*?)(?=^## )", text, re.MULTILINE | re.DOTALL)
    assert match, "README.md has no `## Hardware` section followed by another section"
    return match.group(1)


def _drift(section: str, models: set[str]) -> tuple[set[str], set[str]]:
    """(models the registry offers and the section leaves out,
        models the section names and the registry does not offer)."""
    named = set(_MODEL.findall(section))
    return models - named, named - models


def test_the_extractors_read_something():
    models = _registry_models()
    named = set(_MODEL.findall(_hardware_section(README.read_text(encoding="utf-8"))))
    assert len(models) >= 5, models
    assert {"Amp4 Pro", "DAC2 HD"} <= models, models
    assert len(named) >= 5, named
    # Every registry model is one the README's pattern can read at all, or the
    # comparison below would report it missing whatever the README says.
    assert all(_MODEL.fullmatch(m) for m in models), models


def test_the_readme_names_exactly_the_boards_the_registry_offers():
    missing, unknown = _drift(
        _hardware_section(README.read_text(encoding="utf-8")), _registry_models(),
    )
    assert not missing, (
        f"README.md § Hardware leaves out {sorted(missing)}, which the setup wizard offers"
    )
    assert not unknown, (
        f"README.md § Hardware names {sorted(unknown)}, which hardware/registry.py does not "
        "offer: a unit with that board cannot be set up"
    )


def test_a_board_left_out_or_added_goes_red():
    section = _hardware_section(README.read_text(encoding="utf-8"))
    models = _registry_models()
    assert _drift(section, models) == (set(), set())

    without = section.replace("DAC2 HD", "")
    assert _drift(without, models)[0] == {"DAC2 HD"}

    added = section + "\nAlso the Amp3.\n"
    assert _drift(added, models)[1] == {"Amp3"}
