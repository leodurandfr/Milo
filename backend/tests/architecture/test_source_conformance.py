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
    "bluetooth": ("A", {"source.py"}, {"routes.py", "data.py", "models.py"}),
    "mac": ("A", {"source.py"}, {"routes.py", "data.py", "models.py"}),
    # B — passive player: external control, rich metadata. routes.py exists only
    # for what the sender can't deliver (binary artwork); Qobuz needs none.
    "airplay": ("B", {"source.py", "metadata_reader.py", "routes.py"}, set()),
    "dlna": ("B", {"source.py", "metadata_reader.py", "routes.py"}, set()),
    "qobuz": ("B", {"source.py", "monitor.py"}, {"routes.py"}),
    # C — active player: controlled from Milō's UI, rich metadata.
    "spotify": ("C", {"source.py", "websocket.py", "models.py"}, {"routes.py"}),
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
    """One injection shape for all 10, so dependencies.py stays uniform."""
    params = list(inspect.signature(source_class(source_id).__init__).parameters)
    assert params[:5] == [
        "self", "config", "state_machine", "settings_service", "systemd_manager"
    ], (
        f"{source_id}: unexpected constructor signature {params} — the first four "
        f"injected services are fixed (extra ones may follow, e.g. camilladsp_service)"
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
    """The `{Name}Source` class body, with the names its own package exports."""
    tree = ast.parse((SOURCES_ROOT / source_id / "source.py").read_text())
    package_names = {
        alias.asname or alias.name.split(".")[-1]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").startswith(f"backend.sources.{source_id}")
        for alias in node.names
    }
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    assert classes, f"{source_id}/source.py declares no class — the extractor is broken"
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

    `initialize`/`refresh_metadata` are exempt: they are the base contract, and
    bringing a collaborator up is the source's job even when nothing else is.
    """
    cls, package_names = _source_ast(source_id)
    methods = [
        m for m in cls.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert methods, f"{source_id}: no methods parsed — the extractor is broken"

    init = next((m for m in methods if m.name == "__init__"), None)
    collaborators = {}
    for node in ast.walk(init) if init else []:
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
    if not collaborators:
        pytest.skip(f"{source_id} constructs no collaborator from its own package")

    exposed = set()
    for method in methods:
        if _is_property(method):
            exposed |= _self_attrs(method)

    for method in methods:
        if _is_property(method) or method.name.startswith("_"):
            continue
        if method.name in ("initialize", "refresh_metadata"):
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
