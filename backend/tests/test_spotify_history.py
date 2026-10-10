"""The Spotify history Milō keeps (sources/spotify/history.py): the queue
page's recently played tab, which each Spotify app keeps for its own player."""
import json

from backend.sources.spotify.history import SpotifyHistory


def played(n):
    return {"uri": f"spotify:track:{n}", "played_at": n}


async def test_the_history_keeps_the_latest_five_hundred(tmp_path):
    """What the Spotify apps keep (measured on the Mac app): the oldest play
    goes when a new one comes past it."""
    history = SpotifyHistory(tmp_path / "history.json")
    await history.initialize()
    for n in range(SpotifyHistory.LIMIT + 3):
        history.add("someone", played(n))
    await history.save()

    tracks = history.tracks("someone")
    assert len(tracks) == SpotifyHistory.LIMIT
    assert tracks[0]["played_at"] == SpotifyHistory.LIMIT + 2
    assert tracks[-1]["played_at"] == 3


async def test_each_account_has_its_own_history_and_it_survives_a_restart(tmp_path):
    """Switching profiles must not show one account what another played, and
    a backend restart must not empty the tab."""
    file = tmp_path / "history.json"
    history = SpotifyHistory(file)
    await history.initialize()
    history.add("first", played(1))
    history.add("second", played(2))
    await history.save()

    restarted = SpotifyHistory(file)
    await restarted.initialize()
    assert restarted.tracks("first") == [played(1)]
    assert restarted.tracks("second") == [played(2)]
    assert restarted.tracks("third") == []
    assert json.loads(file.read_text())["schema_version"] == SpotifyHistory.SCHEMA_VERSION

    # A forgotten profile takes what it played with it.
    assert restarted.forget("first")
    await restarted.save()
    assert "first" not in json.loads(file.read_text())["accounts"]


async def test_an_unreadable_history_costs_the_list_not_the_source(tmp_path, caplog):
    """A history that is not JSON must not keep the Spotify source from
    starting (its initialize carries the history's): the list starts anew,
    and the next play writes a sound file over it."""
    file = tmp_path / "history.json"
    file.write_text("{not json")
    history = SpotifyHistory(file)
    await history.initialize()
    assert history.tracks("someone") == []
    assert "unreadable" in caplog.text

    history.add("someone", played(1))
    await history.save()
    assert json.loads(file.read_text())["accounts"]["someone"] == [played(1)]
