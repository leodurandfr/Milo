"""Structural guardrail: every audio card declares the GPIOs its overlay holds.

`AUDIO_CARDS[*]["reserved_pins"]` is the one list three consumers read through
`registry.reserved_pins()`: `HardwareConfigRequest` refuses a rotary, IR or LED
pin on it, `GET /api/settings/hardware-config` serves it so the Hardware page
drops those pins from its dropdowns, and the setup wizard turns off a kept
encoder, IR receiver or power LED that sits on one. A card added without the
key is a card for which all three offer every pin again — I2S included — and
the first sign is a sound card that does not probe after the reboot.

The extractor asserts its own output is non-trivial first, so a registry that
lost its overlays fails here instead of passing on an empty set.
"""
import pytest

from backend.config.constants import SELECTABLE_GPIO_PINS
from backend.hardware.registry import AUDIO_CARDS, reserved_pins

# Every HiFiBerry overlay routes the HAT's audio over I2S1 on these four pins.
I2S_PINS = {18, 19, 20, 21}

OVERLAY_CARDS = [card_id for card_id, card in AUDIO_CARDS.items() if card["overlay"]]


def test_the_registry_has_overlay_cards_to_check():
    assert len(OVERLAY_CARDS) >= 8, f"only {len(OVERLAY_CARDS)} overlay cards found"


@pytest.mark.parametrize("card_id", OVERLAY_CARDS)
def test_every_overlay_card_declares_the_pins_it_holds(card_id):
    declared = AUDIO_CARDS[card_id].get("reserved_pins")
    assert declared, f"{card_id} declares no reserved_pins: every pin would be offered for it"
    assert len(set(declared)) == len(declared), f"{card_id} lists a pin twice"
    assert I2S_PINS <= set(declared), f"{card_id} leaves an I2S pin selectable"
    assert set(declared) <= set(SELECTABLE_GPIO_PINS), (
        f"{card_id} reserves a pin outside the selectable range: nothing could offer it anyway"
    )


def test_no_card_holds_no_pin():
    assert reserved_pins("none") == frozenset()

