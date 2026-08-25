# backend/tests/test_classifiers.py
"""The small pure predicates the backend branches on.

Four of the 29 greens of the Lot A eviscration sweep: each could be replaced by
a constant with the whole suite green, and each decides a branch on its own.
Replaced by their neutral they read, respectively, "every exception is the
network's fault", "every card is a DAC", "every genre is valid" and "every
satellite shows an empty name".

None of them restates its table: the card and hostname tests derive their
operands from the registry and the map, so adding an entry needs no test edit.
What is asserted is the classification, not the data.
"""
import asyncio

import aiohttp
import pytest

from backend.config.constants import HOSTNAME_DISPLAY_NAMES, get_client_display_name
from backend.hardware.registry import AUDIO_CARDS, is_dac_card
from backend.sources.radio.genres import VALID_GENRES, is_valid_genre
from backend.shared.network import is_network_error


class TestIsNetworkError:
    """`shared/network.py` — what the catalog clients retry rather than report.

    Consumers: the Radio Browser, Podcast Index and Navidrome clients, which
    turn a True into `NetworkUnavailableError` and a soft "check your
    connection". A False that became True would bury a genuine parse or auth
    failure behind an offline banner, and the reverse would report a flaky DNS
    lookup as a broken catalog.
    """

    @pytest.mark.parametrize("exc", [
        asyncio.TimeoutError(),
        aiohttp.ServerConnectionError(),
        aiohttp.ClientOSError(),
    ])
    def test_a_transient_connectivity_failure_is_one(self, exc):
        assert is_network_error(exc) is True

    @pytest.mark.parametrize("exc", [
        ValueError("bad json"),
        KeyError("missing field"),
        RuntimeError("boom"),
    ])
    def test_a_failure_that_is_not_the_network_is_not_one(self, exc):
        assert is_network_error(exc) is False


class TestIsDacCard:
    """`hardware/registry.py` — a DAC has no built-in amplifier.

    Consumer: the hardware config, which derives `volume_control` from it when
    the request leaves it None. Getting it wrong hands volume control to a card
    that cannot attenuate, or takes it away from one that can.
    """

    def test_every_card_the_registry_calls_a_dac_is_one(self):
        dacs = [k for k, v in AUDIO_CARDS.items() if v.get("category") == "dac"]
        assert dacs, "registry declares no DAC at all — the test below proves nothing"
        assert all(is_dac_card(k) for k in dacs)

    def test_no_card_of_another_category_is_one(self):
        others = [k for k, v in AUDIO_CARDS.items() if v.get("category") != "dac"]
        assert others, "registry declares nothing but DACs — nothing to discriminate"
        assert not any(is_dac_card(k) for k in others)

    def test_an_id_the_registry_does_not_know_is_not_one(self):
        assert is_dac_card("no-such-card") is False


class TestIsValidGenre:
    """`sources/radio/genres.py` — the gate on a genre browse.

    Consumer: the radio catalog browse, which sends the genre on to Radio
    Browser as a tag. Accepting anything turns a typo into an empty station
    list with no explanation.
    """

    def test_a_declared_genre_is_valid_whatever_its_case_and_padding(self):
        one = next(iter(VALID_GENRES))
        assert is_valid_genre(one) is True
        assert is_valid_genre(f"  {one.upper()}  ") is True

    def test_a_genre_nobody_declared_is_not(self):
        assert is_valid_genre("not-a-genre-anyone-declared") is False

    @pytest.mark.parametrize("blank", ["", "   ", None])
    def test_nothing_at_all_is_not_a_genre(self, blank):
        assert is_valid_genre(blank) is False


class TestClientDisplayName:
    """`config/constants.py` — the name a satellite carries in the UI.

    Consumer: the multiroom client list. The map exists because a satellite
    reports its raw hostname; an unmapped host must keep its own name rather
    than lose it.
    """

    def test_a_mapped_hostname_is_renamed(self):
        for hostname, shown in HOSTNAME_DISPLAY_NAMES.items():
            assert get_client_display_name(hostname) == shown
            assert shown != hostname, "the map would be doing nothing here"

    def test_an_unmapped_hostname_keeps_itself(self):
        assert get_client_display_name("canape-de-leo") == "canape-de-leo"
