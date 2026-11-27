# backend/tests/test_radio_stream_agent.py
"""`stream_agent.agent_for` — which host a station is reached on with the
splice-free agent, and which keeps the player's default.

Both of the radio's connections ask it: mpv's load (`RadioSource._tune`) and
every Shazam capture. A host wrongly in the list is a station that stops
playing (SomaFM answers `curl/` with a 403, measured 2026-11-27); a host
wrongly out of it is a tune that opens on Infomaniak's spliced ad.
"""
import pytest

from backend.sources.radio.stream_agent import SPLICE_FREE_AGENT, agent_for


@pytest.mark.parametrize("url", [
    "https://nova-odn.ice.infomaniak.ch/nova-odn-256.aac",
    "http://radionova.ice.infomaniak.ch/radionova-256.aac",
    "https://NOVA-ODN.ICE.INFOMANIAK.CH/nova-odn-256.aac",
    "https://nova-odn.ice.infomaniak.ch./nova-odn-256.aac",
    "https://user@nova-odn.ice.infomaniak.ch:8443/nova?x=1",
])
def test_an_infomaniak_stream_host_gets_the_splice_free_agent(url):
    assert agent_for(url) == SPLICE_FREE_AGENT


@pytest.mark.parametrize("url", [
    "https://ice2.somafm.com/groovesalad-128-mp3",
    "https://icecast.walmradio.com:8443/classic",
    "https://icecast.radiofrance.fr/fip-hifi.aac",
    # A name that merely ends in the domain's letters is another domain.
    "https://notinfomaniak.ch/stream.aac",
    "https://infomaniak.ch.example.com/stream.aac",
    # The domain in the path or the query is not the host.
    "https://example.com/infomaniak.ch/stream.aac",
    "not a url",
    "",
])
def test_every_other_host_keeps_the_players_default(url):
    """None, not an agent: the caller then sends nothing and the player's own
    default goes out, which is what played everywhere before."""
    assert agent_for(url) is None
