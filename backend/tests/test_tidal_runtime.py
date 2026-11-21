"""Tidal's runtime tree, which the image does not carry and a unit fetches itself.

The daemon is Tidal's proprietary SDK with iFi's device certificate, so the
image no longer ships `/opt/milo/tidal-connect`: the first selection of Tidal
(or its tile enabled in the Dock) asks the root helper for it —
`milo-deploy-update install-tidal-runtime` — and the screen reads the
source's availability meanwhile. Driven through the Tidal world
(`tests/tidal_world.py`) with the helper as the backend sees it: an argv, an
exit status, and the tree renamed into place or not.

If these fail, a fresh unit's Tidal tile either never works (no install is
asked for, or the source starts a daemon whose launcher exits at once on a
missing tree, every 5 s), or works only after a gesture nobody is told to make.
"""
import logging

import pytest

from backend.config.constants import DEPLOY_UPDATE_CMD
from backend.sources.tidal.runtime import INSTALL_SUBCOMMAND
from backend.tests.golden.harness import settle
from backend.tests.tidal_world import TidalWorld

# The verb's two spellings, here and in milo-deploy-update's dispatch, are held
# equal by tests/architecture/test_daemon_rendezvous.py.
INSTALL = ("sudo", "-n", DEPLOY_UPDATE_CMD, INSTALL_SUBCOMMAND)
UNIT = "milo-tidal.service"


@pytest.fixture
async def world(monkeypatch, tmp_path):
    w = TidalWorld(monkeypatch, tmp_path, runtime_installed=False)
    yield w
    await w.leave()
    await w.machine.shutdown_sources()


def _on_wire(world) -> dict:
    """The last `source/state` a client was sent: what the card shows. The
    state composed live (`world.state()`) reads `availability()` itself, so it
    would hold even if the source never told anyone its availability moved."""
    states = world.published()
    assert states, "nothing was published"
    return states[-1]


def _unit_starts(world) -> int:
    return sum(1 for call in world.systemd.start.await_args_list if UNIT in call.args)


async def test_a_first_selection_installs_the_runtime_then_starts_tidal(world):
    """The start finds no tree: it asks the helper for one and fails, so the
    card says why; the tree landing while Tidal is still selected is the retry,
    and the daemon starts with no second gesture."""
    await world.select()

    assert world.helper.calls == [INSTALL]
    state = _on_wire(world)
    assert state["service"] == "failed"
    assert state["availability"]["tidal"] == "installing"
    # The launcher would exit on the missing tree, and Restart= every 5 s.
    assert _unit_starts(world) == 0

    await world.helper.finishes(installed=True)

    state = _on_wire(world)
    assert state["availability"]["tidal"] is None
    assert state["source"] == "tidal" and state["service"] == "running"
    assert _unit_starts(world) == 1
    assert world.daemon.up


async def test_a_failed_download_is_said_and_selecting_tidal_again_retries_it(world, caplog):
    await world.select()
    with caplog.at_level(logging.ERROR):
        await world.helper.finishes(installed=False)

    # The helper's output reaches no journal but this line: it names the
    # builder's cause, not the wrapper the helper always ends on.
    # The builder's cause is a FATAL heading and the libraries it names on the
    # lines below it; the helper's wrapper names nothing.
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    cause = [m for m in errors if "Tidal Connect runtime not installed" in m]
    assert len(cause) == 1, errors
    assert "FATAL: libraries below this kernel's page size:" in cause[0]
    assert "libasound.so.2" in cause[0] and "libcrypto.so.1.1" in cause[0]
    assert "could not be built" not in cause[0]

    # On the wire, so the card leaves "Installing…" for its Retry button.
    state = _on_wire(world)
    assert state["availability"]["tidal"] == "download_failed"
    assert state["service"] == "failed"
    assert world.helper.calls == [INSTALL]

    # The card's Retry re-selects the source, which a failed start lets through.
    await world.select()

    assert world.helper.calls == [INSTALL, INSTALL]
    assert _on_wire(world)["availability"]["tidal"] == "installing"

    await world.helper.finishes(installed=True)
    assert _on_wire(world)["service"] == "running"


async def test_selecting_tidal_during_the_download_starts_no_second_one(world):
    """One build at a time: a second would remove the first's staging tree."""
    await world.select()
    await world.select()

    assert world.helper.calls == [INSTALL]
    assert _on_wire(world)["availability"]["tidal"] == "installing"


async def test_a_tree_that_lands_after_tidal_was_left_starts_nothing(world):
    """The retry is the user's own selection, made for them only while it is
    still the state: a Tidal left meanwhile stays left."""
    await world.select()
    await world.leave()

    await world.helper.finishes(installed=True)

    assert _on_wire(world)["source"] == "none"
    assert _on_wire(world)["availability"]["tidal"] is None
    assert _unit_starts(world) == 0


async def test_the_dock_fetches_a_missing_runtime_and_leaves_a_present_one(world):
    """Enabling Tidal in the Dock prepares the tree, so a first selection finds
    it; once there, a second enable asks nothing of the helper."""
    world.source.runtime.prepare()
    await settle()

    # Tidal is not selected: this publish is the runtime's alone.
    assert world.helper.calls == [INSTALL]
    assert _on_wire(world)["availability"]["tidal"] == "installing"

    await world.helper.finishes(installed=True)
    world.source.runtime.prepare()
    await settle()

    assert world.helper.calls == [INSTALL]
    assert _on_wire(world)["availability"]["tidal"] is None


async def test_an_installed_runtime_never_reaches_the_helper(monkeypatch, tmp_path):
    w = TidalWorld(monkeypatch, tmp_path)
    await w.select()

    assert w.helper.calls == []
    assert w.state()["service"] == "running"
    await w.leave()
    await w.machine.shutdown_sources()
