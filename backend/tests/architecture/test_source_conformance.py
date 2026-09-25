"""Structural guardrail: the 10 audio sources vs their declared family.

`CLAUDE.md § Audio sources` declares the target shape — families A (mute
receiver), B (passive player), C (active player) — with a per-family file
layout, a `BaseAudioSource` contract and a logger-namespace convention. Those
rules were written after most of the sources were, and until now *nothing
verified that a source actually matches its family*. A new source can be
written against the wrong family, or an old one can drift, with no signal.

This test derives the source list from the typed `AudioSource` enum (not a
hand-written fixture), so a source added to the enum without a matching module
layout fails here rather than at runtime on the appliance.

Doctrine note (same as the Milo-Mac contract test and the frontend guardrails):
every extractor asserts its own output is non-trivial first, so a broken parse
fails loudly instead of passing on an empty source list.
"""
import ast
import importlib
import inspect
import re
from pathlib import Path

import pytest

from backend.core.audio_source import BaseAudioSource
from backend.core.models.audio_state import AudioSource

SOURCES_ROOT = Path(__file__).resolve().parents[2] / "sources"

# The family of each source, and what that family prescribes. `required` and
# `forbidden` are the modules the family pins; anything else is a source-specific
# helper and deliberately unconstrained (radio's shazam.py, mac's log_patterns.py
# — a difference is only a finding when the *same* problem got two answers).
FAMILIES = {
    # A — mute receiver: external control, no rich metadata. Commands ride the
    # generic /api/audio/control/{source} endpoint, so no dedicated router.
    "mac": ("A", {"source.py"}, {"routes.py", "data.py", "models.py"}),
    # B — passive player: external control, rich metadata. routes.py exists only
    # for what the sender can't deliver (binary artwork); Qobuz needs none.
    "airplay": ("B", {"source.py", "metadata_reader.py", "routes.py"}, set()),
    "qobuz": ("B", {"source.py", "monitor.py"}, {"routes.py"}),
    # C — active player: controlled from Milō's UI, rich metadata.
    "spotify": ("C", {"source.py", "websocket.py", "models.py"}, {"routes.py"}),
    # Tidal is Spotify's shape with a Unix socket where the WebSocket is. No
    # models.py because every command it accepts is param-less: the tisoc
    # protocol has no seek, so nothing carries a payload.
    "tidal": ("C", {"source.py", "controller_socket.py"}, {"routes.py", "data.py"}),
    # Bluetooth is family C on the same terms as Tidal — Milō draws the track
    # and drives the transport — but with the feed split in two: BlueALSA
    # (monitor.py) answers "who is connected", BlueZ AVRCP (avrcp.py) answers
    # "what is playing". No models.py, every command is param-less: AVRCP has
    # no seek. No routes.py either — AVRCP carries no cover art to serve.
    "bluetooth": ("C", {"source.py", "avrcp.py"}, {"routes.py", "data.py", "models.py"}),
    "radio": ("C", {"source.py", "routes.py", "data.py", "models.py"}, set()),
    "podcast": ("C", {"source.py", "routes.py", "data.py", "models.py"}, set()),
    "cd": ("C", {"source.py", "routes.py", "data.py", "models.py"}, set()),
    "music_library": ("C", {"source.py", "routes.py", "data.py", "models.py"}, set()),
}

# Command names that mean "tear playback down and go back to idle". Family C
# converged on `stop`; these are the drifted spellings that must not come back
# (a second name for one concept forces every caller — and the hardware
# dispatcher above all — to special-case the source).
BANNED_COMMAND_ALIASES = {
    "stop_playback": "stop",
    "stop_play": "stop",
    "next_track": "next",
    "prev_track": "prev",
    "previous": "prev",
    "toggle_play_pause": "playpause",
    "play_pause": "playpause",
}

# Public API of BaseAudioSource. A source customises behaviour through the
# _do_*/_handle_command hooks; overriding a public method bypasses the state and
# validation the base class wraps around them.
SEALED_PUBLIC_METHODS = ("start", "stop", "command")

# Forbidden by CLAUDE.md: status is broadcast over WS, never polled.
FORBIDDEN_METHOD_NAMES = ("status", "_get_status", "get_status")


def source_ids():
    """Every real audio source, from the typed enum (NONE is not a source)."""
    ids = sorted(s.value for s in AudioSource if s is not AudioSource.NONE)
    assert len(ids) >= 10, (
        f"AudioSource enum yielded only {ids} — the extractor is broken"
    )
    return ids


SOURCE_IDS = source_ids()


def source_class(source_id):
    """The source class, reached the way dependencies.py reaches it.

    Going through the package (not `.source`) is deliberate: it also proves the
    package facade still exports the one name the DI container imports.
    """
    package = importlib.import_module(f"backend.sources.{source_id}")
    exported = getattr(package, "__all__", [])
    assert len(exported) == 1, (
        f"backend.sources.{source_id}.__all__ = {exported}; expected exactly one "
        f"name, the {{Name}}Source class dependencies.py imports. Everything else "
        f"is imported from its own submodule — see CLAUDE.md § Audio sources."
    )
    return getattr(package, exported[0])


def test_every_enum_source_has_a_module_and_a_family():
    """The enum, the filesystem and the family map agree on the source list."""
    on_disk = {
        p.name for p in SOURCES_ROOT.iterdir()
        if p.is_dir() and not p.name.startswith("__")
    }
    assert len(on_disk) >= 10, f"only {on_disk} found under sources/ — broken glob"
    assert on_disk == set(SOURCE_IDS), (
        f"sources/ directories {sorted(on_disk)} != AudioSource enum {SOURCE_IDS}"
    )
    assert set(FAMILIES) == set(SOURCE_IDS), (
        f"FAMILIES covers {sorted(FAMILIES)} but the enum declares {SOURCE_IDS} — "
        f"a new source must be assigned a family here"
    )


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_family_module_layout(source_id):
    """Required modules exist; modules the family rules out are absent."""
    family, required, forbidden = FAMILIES[source_id]
    present = {p.name for p in (SOURCES_ROOT / source_id).glob("*.py")}
    assert present, f"no .py files under sources/{source_id} — broken glob"

    missing = required - present
    assert not missing, (
        f"{source_id} (family {family}) is missing {sorted(missing)}"
    )
    extra = forbidden & present
    assert not extra, (
        f"{source_id} (family {family}) must not define {sorted(extra)} — "
        f"see the family table in CLAUDE.md § Audio sources"
    )


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_source_class_honours_the_base_contract(source_id):
    """Hooks are overridden; the public API and status()/-likes are not."""
    cls = source_class(source_id)
    assert issubclass(cls, BaseAudioSource)

    assert "_do_start" in cls.__dict__ or any(
        "_do_start" in base.__dict__
        for base in cls.__mro__[1:]
        if base is not BaseAudioSource
    ), f"{cls.__name__} must implement _do_start()"

    for name in SEALED_PUBLIC_METHODS:
        owner = next(b for b in cls.__mro__ if name in b.__dict__)
        assert owner is BaseAudioSource, (
            f"{cls.__name__} overrides the public {name}() (in {owner.__name__}) — "
            f"customise via _do_start/_do_stop/_handle_command instead"
        )

    for name in FORBIDDEN_METHOD_NAMES:
        assert not hasattr(cls, name), (
            f"{cls.__name__} defines {name}() — status is broadcast over WS, "
            f"never polled (CLAUDE.md § Audio sources)"
        )


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_command_names_use_the_canonical_spelling(source_id):
    """No source reintroduces a second name for an existing concept."""
    cls = source_class(source_id)
    for cmd in cls.COMMANDS:
        canonical = BANNED_COMMAND_ALIASES.get(cmd)
        assert canonical is None, (
            f"{cls.__name__}.COMMANDS uses '{cmd}'; the canonical name for that "
            f"concept is '{canonical}'"
        )


def logger_declarations():
    """Every getLogger() call in a source sub-module, as (source, file, arg).

    `source.py` is skipped: `BaseAudioSource.__init__` owns `source.{id}` itself.
    """
    found = []
    for source_id in SOURCE_IDS:
        for path in sorted((SOURCES_ROOT / source_id).glob("*.py")):
            if path.name in ("__init__.py", "source.py"):
                continue
            for raw in re.findall(r"getLogger\(\s*(.+?)\s*\)", path.read_text()):
                found.append((source_id, path.name, raw.strip()))
    assert len(found) >= 20, (
        f"only {len(found)} getLogger calls found across sources/ — "
        f"the extractor is broken"
    )
    return found


@pytest.mark.parametrize("source_id,filename,arg", logger_declarations())
def test_logger_namespaces(source_id, filename, arg):
    """Sub-modules hang under `source.{id}.*`; routers use __name__.

    The hierarchy matters: `BaseAudioSource.__init__` creates `source.{id}`, so a
    sub-module logging elsewhere escapes the level and handler configured for its
    source. The legacy `feature.*` namespace is retired.
    """
    if filename == "routes.py":
        assert arg == "__name__", (
            f"{source_id}/{filename}: routers use logging.getLogger(__name__), "
            f"found {arg}"
        )
        return

    assert arg.startswith(("'", '"')), (
        f"{source_id}/{filename}: expected a literal 'source.{source_id}.<sub>' "
        f"logger name, found {arg}"
    )
    assert ast.literal_eval(arg).startswith(f"source.{source_id}."), (
        f"{source_id}/{filename}: expected a 'source.{source_id}.<sub>' logger, "
        f"found {arg}"
    )


def test_no_source_reintroduces_a_status_endpoint():
    """No `GET /<source>/status` and no `POST /<source>/restart`, in any router.

    Status is broadcast over WS only, and restart is a systemd/admin concern —
    both are cheap to re-add by reflex, which is why they are pinned here.
    """
    routers = sorted(SOURCES_ROOT.glob("*/routes.py"))
    assert len(routers) >= 5, f"only {routers} found — broken glob"
    for path in routers:
        for method, route in re.findall(
            r'@router\.(get|post|put|patch|delete)\(\s*["\']([^"\']+)', path.read_text()
        ):
            assert route.rstrip("/") != "/status", (
                f"{path.parent.name}/routes.py exposes {method.upper()} /status — "
                f"status is broadcast over WS only"
            )
            assert route.rstrip("/") != "/restart", (
                f"{path.parent.name}/routes.py exposes {method.upper()} /restart — "
                f"restart is a systemd/admin concern"
            )


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_source_constructor_signature(source_id):
    """One injection shape for all of them, so dependencies.py stays uniform.

    Exact, not a prefix: an extra injected service that the source then stores
    nowhere is a dependency the wiring pays for and nothing reads — which is
    what `camilladsp_service` was on Mac and Bluetooth.
    """
    params = list(inspect.signature(source_class(source_id).__init__).parameters)
    assert params == [
        "self", "config", "state_machine", "settings_service", "systemd_manager"
    ], (
        f"{source_id}: unexpected constructor signature {params} — the four "
        f"injected services are fixed, and no source takes a fifth"
    )


# =============================================================================
# Collaborator ownership (CLAUDE.md § Audio sources — "expose, don't proxy")
# =============================================================================
#
# routes.py can only reach the source instance (that is all
# `make_source_dependency` injects), so a source's non-playback services have to
# be reachable *through* it. Radio, Podcast and CD do that by exposing the
# service as a property and letting routes call it. Music Library instead grew
# nine forwarding/orchestration methods on the audio source, which is how the
# network-share lifecycle — config, mounts, boot remount — ended up owned by a
# class whose job is playing audio, and how its two collaborators each built
# their own Navidrome client.


def _source_ast(source_id):
    """The `{Name}Source` class body, with the classes its own package exports.

    Classes only, resolved by importing them: a collaborator is a service the
    source builds. Qobuz's `self._account_cached = is_connected(...)` stores
    the bool a package *function* returns, and counting it made the account
    flag read as a service no property exposed.
    """
    tree = ast.parse((SOURCES_ROOT / source_id / "source.py").read_text())
    package_names = {
        alias.asname or alias.name.split(".")[-1]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").startswith(f"backend.sources.{source_id}")
        for alias in node.names
        if inspect.isclass(getattr(importlib.import_module(node.module), alias.name, None))
    }
    # By name, not by position: a module on the session model declares its
    # Session subclass first.
    classes = [
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name.endswith("Source")
    ]
    assert len(classes) == 1, f"{source_id}/source.py: no single {{Name}}Source class — the extractor is broken"
    return classes[0], package_names


def _self_attrs(node):
    return {
        n.attr for n in ast.walk(node)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "self"
    }


def _is_property(method):
    return any(
        isinstance(d, ast.Name) and d.id == "property" for d in method.decorator_list
    )


def _collaborators(cls, package_names):
    """`self.<attr>` assigned something the source's own package builds.

    Every method, not just `__init__`: four of the ten sources construct
    their collaborator in `_do_start` — airplay's reader, qobuz's monitor,
    spotify's ws client, tidal's controller — because it holds a socket that
    must not outlive a stopped source. Reading `__init__` alone found nothing
    for them, and the rule below then *skipped* them, so it covered a subset of
    the sources while its name claimed all of them.
    """
    collaborators = {}
    for node in ast.walk(cls):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        func = node.value.func
        built = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if built not in package_names:
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                collaborators[target.attr] = built
    return collaborators


# Sources whose package holds no class for the source to own: mac's helpers
# (log_patterns, mdns) are plain functions. Any *other* source landing here
# means the extractor stopped seeing a construction site — which is exactly how
# this rule came to cover 5 of 11 sources in silence.
NO_COLLABORATOR_SOURCES = {"mac"}

# Public methods where reaching a collaborator is the source's own work rather
# than a proxy for a caller. `initialize`/`shutdown`/`refresh_metadata` are the
# base contract — bringing a collaborator up (and down: the CD's udev monitor
# outlives every start/stop) is the source's job even when nothing else is. `on_shazam_setting_changed` is the same kind: its caller
# (api/settings.py::set_radio_settings) wants the *source* to react to a global
# toggle, and the method re-arms `_shazam_candidate` against the in-band feed —
# state nobody holding `source.shazam` could reason about. Exposing the service
# to satisfy the rule would add a property no caller reads. `availability` is
# base contract too: the state machine asks it of every registered source, and
# Qobuz answers from its running monitor — the source describing itself, not a
# service handed to routes.py.
COLLABORATOR_OWNER_METHODS = (
    "initialize", "shutdown", "refresh_metadata", "on_shazam_setting_changed",
    "availability",
)


def test_collaborator_extraction_reaches_every_source_but_the_named_ones():
    """The skip in the rule below is a claim, and this is what checks it."""
    none_found = {
        source_id for source_id in SOURCE_IDS
        if not _collaborators(*_source_ast(source_id))
    }
    assert none_found == NO_COLLABORATOR_SOURCES, (
        f"sources constructing no collaborator: {sorted(none_found)}; expected "
        f"{sorted(NO_COLLABORATOR_SOURCES)}. A source that stops yielding one is "
        f"skipped by test_collaborators_are_exposed_not_proxied, not checked by it."
    )


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_collaborators_are_exposed_not_proxied(source_id):
    """A collaborator a public source method touches is exposed as a property.

    Crossing the public boundary is the discriminator, not the collaborator
    itself: cd's `_reader`, radio's `_artwork` and bluetooth's agent/monitor are
    referenced only from private playback code and are exactly where they
    belong. One that a *public* method reaches is one routes.py needs — and then
    the source must hand it over (`source.shares`, `source.station_data`) rather
    than grow a forwarding method per call, which is a second API surface that
    drifts and puts non-playback work on the audio source.

    The exemptions are named in `COLLABORATOR_OWNER_METHODS`, with the reason
    each one is the source's own work rather than a forwarded call.
    """
    cls, package_names = _source_ast(source_id)
    methods = [
        m for m in cls.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert methods, f"{source_id}: no methods parsed — the extractor is broken"

    collaborators = _collaborators(cls, package_names)
    if not collaborators:
        pytest.skip(f"{source_id} constructs no collaborator from its own package")

    exposed = set()
    for method in methods:
        if _is_property(method):
            exposed |= _self_attrs(method)

    for method in methods:
        if _is_property(method) or method.name.startswith("_"):
            continue
        if method.name in COLLABORATOR_OWNER_METHODS:
            continue
        for attr in sorted(_self_attrs(method) & set(collaborators)):
            assert attr in exposed, (
                f"{source_id}: public {method.name}() reaches self.{attr} "
                f"({collaborators[attr]}), which no property exposes — routes.py "
                f"should call that service directly. See CLAUDE.md § Audio sources."
            )


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_one_construction_site_per_collaborator(source_id):
    """Each service class in a source package is built in exactly one module.

    Two owners of one client is two lifecycles: the Music Library source and its
    storage manager each built a NavidromeClient from the same cred file, so they
    held separate HTTP sessions and only one of them had the auth-recovery path
    that drops a stale client. A second construction site is how that starts.
    """
    package = SOURCES_ROOT / source_id
    declared = {}
    for path in package.glob("*.py"):
        if path.name == "models.py":  # Pydantic models are built at every call site
            continue
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.ClassDef):
                declared[node.name] = path.name
    assert declared, f"{source_id}: no classes parsed — the extractor is broken"

    sites = {}
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id in declared:
                sites.setdefault(func.id, set()).add(path.name)
            elif (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id in declared
                and func.attr.startswith("from_")  # alternative constructors
            ):
                sites.setdefault(func.value.id, set()).add(path.name)

    for name, modules in sorted(sites.items()):
        assert len(modules) == 1, (
            f"{source_id}: {name} is constructed in {sorted(modules)} — one owner "
            f"per service; the others take it from the owner."
        )


# The four sources that play through mpv. Derived nowhere: MpvAudioSource's own
# docstring names them, and test_family_module_layout already pins the family.
MPV_SOURCE_IDS = ("radio", "podcast", "cd", "music_library")


@pytest.mark.parametrize("source_id", MPV_SOURCE_IDS)
def test_mpv_sources_attach_through_the_base_class(source_id):
    """An mpv source opens its IPC link in one place, not four.

    Each of them used to build its own MpvController, call connect() inline and
    report the failure itself. Four copies of one act: the report had already
    drifted into two spellings of the same sentence at two levels, and every
    copy duplicated in the journal a line connect() writes better — it names
    what it waited for and for how long. CD holds two of the five call sites,
    so "the library was fixed" is not the same as "the path was fixed".
    """
    text = (SOURCES_ROOT / source_id / "source.py").read_text()
    assert "_attach_mpv(" in text, (
        f"{source_id}: attaches mpv by some other means — the rules below "
        f"would pass on an empty surface"
    )

    tree = ast.parse(text)
    built = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "MpvController"
    ]
    assert not built, f"{source_id}: builds its own MpvController"

    connects = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "connect"
    ]
    assert not connects, f"{source_id}: opens the IPC link itself"




# === Publication (docs: "le fil") ================================================
#
# A source publishes one thing, its view — the live session, the resume point,
# its own details and the commands it takes now — and the base composes that
# view from four hooks (`_session_fields`, `_resume_view`, `_details`,
# `_controls`). The state machine adds everything else and decides what goes
# out. The rules below pin that a source reaches the wire only through those
# hooks and the base's one publish site; the old wire drifted exactly where a
# source hand-built a payload beside the shared primitive.

# Where source code lives: every source package, plus the base the four mpv
# sources share (it publishes on their behalf).
SOURCE_MODULES = sorted(SOURCES_ROOT.rglob("*.py")) + [
    SOURCES_ROOT.parent / "shared" / "mpv_audio_source.py"
]

# The base's publish primitives, and what lies behind them. A source calling
# the state machine or broadcasting the state itself would bypass the view the
# base composes, and with it the empty-string → null rule and the anchor rebase.
PUBLISH_PRIMITIVES = ("_publish", "_publish_changes")
STATE_MACHINE_SINKS = ("update_source_view",)
STATE_EVENTS = ("AudioStateChanged", "SourcePosition")


def _parsed_source_modules():
    parsed = [(path, ast.parse(path.read_text())) for path in SOURCE_MODULES]
    assert len(parsed) >= 40, (
        f"only {len(parsed)} source modules parsed — the glob is broken"
    )
    return parsed


def _publish_calls(source_id):
    """`self._publish()` / `self._publish_changes()` calls in a source.py."""
    tree = ast.parse((SOURCES_ROOT / source_id / "source.py").read_text())
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in PUBLISH_PRIMITIVES
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
    ]


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_every_source_publishes_through_the_base(source_id):
    """Non-triviality first: a source that never calls `_publish()` or
    `_publish_changes()` either stopped announcing itself or found another way
    out — and the rule below would pass on it either way."""
    assert _publish_calls(source_id), (
        f"{source_id}/source.py never calls self._publish() or "
        f"self._publish_changes() — the extractor is broken, or the source "
        f"reaches the wire some other way"
    )


def test_a_source_reaches_the_state_machine_only_through_publish():
    """One publisher: the base's `_publish()`.

    No source module defines `_publish`/`_publish_changes` (an override is a
    second publish site with the base's name), calls
    `state_machine.update_source_view` itself, or broadcasts
    `AudioStateChanged`/`SourcePosition` — the state machine alone decides
    whether a change goes out as a state or as a playhead. A source that
    bypassed it would send a view the base never composed: an empty title
    published as `""`, an anchor not re-stamped across a pause, a
    `source/state` for a tick the wire is supposed to stay silent on.
    """
    found = []
    for path, tree in _parsed_source_modules():
        where = path.relative_to(SOURCES_ROOT.parent)
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in PUBLISH_PRIMITIVES
            ):
                found.append(f"{where}:{node.lineno} defines {node.name}()")
            elif isinstance(node, ast.Attribute) and node.attr in STATE_MACHINE_SINKS:
                found.append(f"{where}:{node.lineno} reaches .{node.attr}")
            elif isinstance(node, ast.Name) and node.id in STATE_EVENTS:
                found.append(f"{where}:{node.lineno} uses {node.id}")
            elif isinstance(node, ast.alias) and node.name in STATE_EVENTS:
                found.append(f"{where} imports {node.name}")
    assert not found, (
        "a source publishes around the base's _publish():\n  " + "\n  ".join(found)
    )


def _captures_resume(cls):
    policy = cls.RESUME_POLICY
    return policy is not None and bool(policy.capture_on)


def _overrides(cls, name):
    """Whether something below BaseAudioSource defines `name`."""
    return any(name in base.__dict__ for base in cls.__mro__ if base is not BaseAudioSource)


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_every_source_says_what_its_session_shows(source_id):
    """`_session_fields` is overridden by every source.

    The base's answer is `{}`: a session with no title, no artwork, no sender
    and no duration — the player would draw an empty card over a source that
    plays, and the bar could never be bounded.
    """
    cls = source_class(source_id)
    assert _overrides(cls, "_session_fields"), (
        f"{cls.__name__} does not override _session_fields() — its session "
        f"would publish nothing but a phase"
    )


def test_some_sources_keep_a_resume_point():
    """Otherwise the rule below checks nothing: the RESUME_POLICY reading is
    what decides which sources it applies to."""
    capturing = [s for s in SOURCE_IDS if _captures_resume(source_class(s))]
    assert capturing, (
        "no source's RESUME_POLICY captures anything — the policy reading is "
        "broken, or every resume point is gone"
    )


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_a_source_that_keeps_a_resume_point_shows_it(source_id):
    """A RESUME_POLICY that captures on some end ⇔ a `_resume_view` override.

    A resume point captured and never viewed is one "play" would bring back
    while the idle screen shows nothing to bring back (the base answers None);
    a view on a source that never captures is dead code reading a point that
    cannot exist.
    """
    cls = source_class(source_id)
    captures = _captures_resume(cls)
    assert _overrides(cls, "_resume_view") is captures, (
        f"{cls.__name__}: RESUME_POLICY captures on "
        f"{sorted(r.value for r in cls.RESUME_POLICY.capture_on) if cls.RESUME_POLICY else []} "
        f"but _resume_view is {'not ' if captures else ''}overridden"
    )


# The commands that start content: each names *what* to play, so a `controls`
# entry for it would be a button with no argument. `playpause` is a remote's
# convenience the wire never offers — `controls` says which of pause/resume is
# the one that does something now (docs: "le fil", §4).
NEVER_A_CONTROL = frozenset({"play_station", "play_episode", "play_context", "playpause"})


def _control_literals(source_id):
    """Every string literal a list/tuple/set display in `_controls()` holds.

    Displays only: `station.get("id")` or `c == "next"` are not controls.
    Returns None when the source does not define `_controls`.
    """
    cls, _ = _source_ast(source_id)
    method = next(
        (
            m for m in cls.body
            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name == "_controls"
        ),
        None,
    )
    if method is None:
        return None
    return {
        elt.value
        for node in ast.walk(method)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set))
        for elt in node.elts
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
    }


def test_the_controls_extractor_reads_every_override():
    """A `_controls` whose literals the parse cannot see would pass the rule
    below on an empty set — so every override must yield some, and at least
    one source must override it."""
    extracted = {s: _control_literals(s) for s in SOURCE_IDS}
    overriding = {s: lits for s, lits in extracted.items() if lits is not None}
    assert overriding, "no source defines _controls() — the extractor is broken"
    empty = sorted(s for s, lits in overriding.items() if not lits)
    assert not empty, (
        f"_controls() of {empty} yields no string literal — the extractor no "
        f"longer reads its shape"
    )


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_controls_name_commands_the_source_takes(source_id):
    """`controls` is the command vocabulary, not a second one (docs: "le fil", §4).

    Every name `_controls()` can return is a key of the source's COMMANDS —
    the UI sends it back verbatim to `/api/audio/control/{source}`, and a name
    COMMANDS lacks is a button that answers 400. None of them starts content
    (`play_station`, `play_episode`, `play_context`: they need an argument the
    button does not have) or is `playpause` (the list already says which of
    pause/resume applies).
    """
    literals = _control_literals(source_id)
    if literals is None:
        pytest.skip(f"{source_id} takes the base's controls (none)")
    commands = set(source_class(source_id).COMMANDS)
    unknown = sorted(literals - commands)
    assert not unknown, (
        f"{source_id}: _controls() can return {unknown}, which COMMANDS "
        f"({sorted(commands)}) does not accept"
    )
    forbidden = sorted(literals & NEVER_A_CONTROL)
    assert not forbidden, (
        f"{source_id}: _controls() offers {forbidden} — a control never starts "
        f"content and is never playpause"
    )


# === The playhead: one aging implementation =====================================
#
# The anchor ({ms, at, rate}) is aged by whoever reads it; the base alone
# stamps it (`_anchor_position`, `_observe_position`, `_clear_position`). Before
# it, AirPlay and Qobuz each aged their own playhead against the loop clock
# (`_position_of`, `_sync_clock`) and four sources pushed it on a timer
# (`broadcast_position_update`) — three implementations of one sum, which
# disagreed on what a pause does to it.

RETIRED_PLAYHEAD_NAMES = frozenset({
    "_position_of", "_sync_clock", "broadcast_position_update", "position_at",
})

# Calls that read a clock.
CLOCK_READS = frozenset({"monotonic", "time", "perf_counter", "_now", "wall_time"})

# (module, name) pairs whose clock arithmetic is a playhead on purpose.
CLOCKED_PLAYHEAD_ALLOWED = {
    # AVRCP's self-counted playhead: a reading of a sender whose BlueZ anchor
    # a Previous invalidated for good, handed to `_observe_position` like any
    # other reading — not an aging of the wire's anchor.
    ("sources/bluetooth/avrcp.py", "_own_playhead_from"),
    # A read Position ages from when it was read while the track plays, as
    # BlueZ's own extrapolation does: read as it was, a feed burst between two
    # polls saw it seconds behind the anchor and pulled the bar back. Still a
    # reading handed to `_observe_position`, which alone moves the anchor.
    ("sources/bluetooth/avrcp.py", "_position_at"),
}


def _is_clock_read(node):
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    return name in CLOCK_READS


def _names_in(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            yield sub.id
        elif isinstance(sub, ast.Attribute):
            yield sub.attr


def _clocked_playheads(path, tree):
    """Arithmetic on a clock reading that names a position or a playhead."""
    where = path.relative_to(SOURCES_ROOT.parent).as_posix()
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp):
            continue
        if not any(_is_clock_read(sub) for sub in ast.walk(node)):
            continue
        for name in set(_names_in(node)):
            if "position" not in name and "playhead" not in name:
                continue
            if (where, name) in CLOCKED_PLAYHEAD_ALLOWED:
                continue
            found.append(f"{where}:{node.lineno} ages {name} against a clock")
    return found


def test_the_clocked_playhead_scan_sees_the_allowed_one():
    """Non-triviality: the scan must still find the one clocked playhead it
    allows, or it has stopped reading the shape it is meant to catch."""
    avrcp = SOURCES_ROOT / "bluetooth" / "avrcp.py"
    tree = ast.parse(avrcp.read_text())
    seen = {
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.BinOp) and any(_is_clock_read(s) for s in ast.walk(node))
        for name in _names_in(node)
    }
    assert "_own_playhead_from" in seen, (
        "the clock-arithmetic scan no longer sees avrcp.py's own playhead — "
        "_is_clock_read or the BinOp walk is broken"
    )


def test_no_source_ages_its_own_playhead():
    """Positions reach the wire only through the base's anchor.

    No source module names a retired aging helper (`_position_of`,
    `_sync_clock`, `broadcast_position_update`, `position_at`), and none does
    arithmetic on a clock reading over a position or playhead: a source that
    ages a position itself publishes a playhead that drifts from the one every
    client computes from the anchor, and a timer that pushes it is the tick
    the wire is designed never to carry.
    """
    found = []
    for path, tree in _parsed_source_modules():
        where = path.relative_to(SOURCES_ROOT.parent).as_posix()
        for node in ast.walk(tree):
            name = (
                node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                else node.attr if isinstance(node, ast.Attribute)
                else node.id if isinstance(node, ast.Name)
                else node.arg if isinstance(node, ast.keyword)
                else None
            )
            if name in RETIRED_PLAYHEAD_NAMES:
                found.append(f"{where}:{node.lineno} names {name}")
        found += _clocked_playheads(path, tree)
    assert not found, (
        "a source ages a playhead outside the base's anchor:\n  " + "\n  ".join(found)
    )
