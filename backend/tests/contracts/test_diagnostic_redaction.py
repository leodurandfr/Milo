"""Offline contract: what the diagnostic report is allowed to say.

The report is built to leave the appliance. A stranger generates it, reads it,
and pastes it into a public issue — so the question this file answers is not
"does the exporter work" but "can a field that should never leave reach it".

The exporter answers that by construction: a structured section is built by
projecting one of the `*_ALLOWED` sets onto its source dict, so an undeclared
key cannot come out. What construction cannot do is *notice* — a field added to
a model upstream is simply absent from the report, silently, and a field added
to the wrong half of the whitelist is worse. That is what this file is for: it
derives the upstream field sets from the models themselves and requires every
one of them to sit in exactly one half, so the day someone adds a share
password, an account token or a device name to a persisted record, this goes red
and a human decides.

The second half of the file proves the enforcement rather than the declaration:
a fixture in which every excluded field holds a unique sentinel is run through
the real report, and no sentinel may survive it. Its counterpart runs first and
matters as much — the allowed sentinels MUST survive, or a broken extractor
would pass this file by producing nothing at all.
"""
import ast
import enum
import functools
import json
import re
import tempfile
import types
import typing
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from backend.api.settings import create_settings_router
from backend.core.models.audio_wire import AudioState
from backend.core.multiroom.models import Client, EqualizerSettings, Zone
from backend.core.settings import SettingsService
from backend.core.system.diagnostic import whitelist as wl
from backend.sources.spotify.source import SpotifySource
from backend.hardware.registry import (
    AUDIO_CARDS,
    DEFAULT_IR_REMOTE,
    DEFAULT_POWER_BUTTON,
    DEFAULT_ROTARY_PINS,
    SCREENS,
    reserved_pins,
)
from backend.hardware.service import HardwareService

REPO_ROOT = Path(__file__).resolve().parents[3]
SETTINGS_MODULE = REPO_ROOT / "backend" / "core" / "settings.py"
MUSIC_LIBRARY_DATA = REPO_ROOT / "backend" / "sources" / "music_library" / "data.py"


# --------------------------------------------------------------------------- #
# Deriving what upstream declares
# --------------------------------------------------------------------------- #

def _leaf_paths(mapping, prefix=""):
    """Every dotted leaf path of a nested declaration.

    An empty dict is a leaf, not an absence: `updates.forced_versions` is `{}`
    in the defaults and is very much a field.
    """
    for key, value in mapping.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            yield from _leaf_paths(value, prefix=f"{path}.")
        else:
            yield path


def _dict_literal_keys(module: Path, function: str, needle: str) -> frozenset:
    """Keys of the first dict literal assigned inside `function`.

    `needle` disambiguates when a function assigns more than one; it must appear
    in the assignment's source segment.
    """
    tree = ast.parse(module.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != function:
            continue
        for inner in ast.walk(node):
            if not (isinstance(inner, ast.Assign) and isinstance(inner.value, ast.Dict)):
                continue
            segment = ast.get_source_segment(module.read_text(), inner) or ""
            if needle not in segment:
                continue
            if not all(
                isinstance(k, ast.Constant) and isinstance(k.value, str)
                for k in inner.value.keys
            ):
                continue
            return frozenset(k.value for k in inner.value.keys)
    return frozenset()


def _settings_paths() -> frozenset:
    """The declared settings surface: the defaults, plus the two sections the
    validator writes without declaring a default for them."""
    paths = set(_leaf_paths(SettingsService().defaults))
    for section in ("bt_remote", "ir_remote"):
        for key in _dict_literal_keys(
            SETTINGS_MODULE, "_validate_and_merge", f"validated_hardware['{section}']"
        ):
            paths.add(f"hardware.{section}.{key}")
    return frozenset(paths)


@functools.cache
def _hardware_paths() -> frozenset:
    """hardware.json's normalised shape, as the Hardware page writes it and the
    collector reads it back.

    The record is written by `PUT /api/settings/hardware-config` and read by
    `HardwareService.get_full_config()`, both real, on a temporary file: a field
    either one gains reaches this set without anyone listing it. The payload
    takes a card with an overlay (the branch that writes the most keys) whose
    reserved pins leave the default ones free, and every peripheral enabled.
    Only the world outside is mocked: `apply` shells `sudo milo-apply-hardware`,
    and `power` reboots this Pi.
    """
    default_pins = {
        *DEFAULT_ROTARY_PINS.values(),
        DEFAULT_IR_REMOTE["gpio_pin"],
        DEFAULT_POWER_BUTTON["led_gpio_pin"],
    }
    card = next(
        key for key, entry in AUDIO_CARDS.items()
        if entry["overlay"] and not reserved_pins(key) & default_pins
    )
    screen = next(key for key, entry in SCREENS.items() if entry["resolution"])
    payload = {
        "audio": {"id": card},
        "screen": {"type": screen},
        "rotary_encoder": {"enabled": True, **DEFAULT_ROTARY_PINS},
        "ir_remote": dict(DEFAULT_IR_REMOTE),
        "power_button": {**DEFAULT_POWER_BUTTON, "enabled": True},
    }
    with tempfile.TemporaryDirectory() as directory:
        hardware_file = Path(directory) / "hardware.json"
        hardware_service = HardwareService()
        hardware_service.hardware_file = hardware_file
        hardware_service.apply = AsyncMock()
        systemd_manager = Mock()
        systemd_manager.power = AsyncMock()
        app = FastAPI()
        app.include_router(
            create_settings_router(
                volume_service=Mock(),
                state_machine=Mock(),
                screen_controller=Mock(),
                systemd_manager=systemd_manager,
                routing_service=Mock(),
                hardware_service=hardware_service,
                settings_service=Mock(),
                multiroom_equalizer_service=Mock(),
                mac_link_calibration=Mock(),
            ),
            prefix="/api/settings",
        )
        response = TestClient(app).put("/api/settings/hardware-config", json=payload)
        assert response.status_code == 200, response.text
        reader = HardwareService()
        reader.hardware_file = hardware_file
        return frozenset(_leaf_paths(reader.get_full_config()))


UPSTREAM = {
    "settings.json": (_settings_paths, wl.SETTINGS_ALLOWED, wl.SETTINGS_EXCLUDED),
    "hardware.json": (_hardware_paths, wl.HARDWARE_ALLOWED, wl.HARDWARE_EXCLUDED),
    "multiroom Client": (
        lambda: frozenset(Client(mac_id="x", name="x", ip="x").to_dict())
        | frozenset(Client.PERSISTED_FIELDS),
        wl.CLIENT_ALLOWED,
        wl.CLIENT_EXCLUDED,
    ),
    "multiroom Zone": (
        lambda: frozenset(Zone(name="x").to_dict()), wl.ZONE_ALLOWED, wl.ZONE_EXCLUDED
    ),
    "EqualizerSettings": (
        lambda: frozenset(EqualizerSettings(custom_gains=[0.0]).to_dict()),
        wl.EQUALIZER_ALLOWED,
        wl.EQUALIZER_EXCLUDED,
    ),
    "music library share": (
        lambda: _dict_literal_keys(MUSIC_LIBRARY_DATA, "add_share", '"has_credentials"'),
        wl.SHARE_ALLOWED,
        wl.SHARE_EXCLUDED,
    ),
    "music library USB key": (
        lambda: _dict_literal_keys(MUSIC_LIBRARY_DATA, "remember_usb", '"mountpoint"'),
        wl.USB_ALLOWED,
        wl.USB_EXCLUDED,
    ),
}


def _covered(path: str, allowed) -> bool:
    """A declared `x.*` covers `x` itself and every key under it: the map is
    one field, its keys data (an empty map in the defaults, or one declared
    with entries, as the BT remote's key map is)."""
    return (
        path in allowed
        or f"{path}.*" in allowed
        or any(path.startswith(entry[:-1]) for entry in allowed if entry.endswith(".*"))
    )


# --------------------------------------------------------------------------- #
# The declaration
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", sorted(UPSTREAM))
def test_every_upstream_field_is_decided(name):
    """Goes red when a model gains a field and nobody said where it belongs.

    This is the whole point of the file. A new persisted field is invisible in
    the report either way — omitted if nobody adds it, leaked if someone adds it
    to the wrong half — and only a rule that reads the model can tell.
    """
    derive, allowed, excluded = UPSTREAM[name]
    undecided = sorted(
        field for field in derive()
        if not _covered(field, allowed) and field not in excluded
    )
    assert not undecided, (
        f"{name} declares field(s) the diagnostic report has not decided about: "
        f"{undecided}. Add each one to the ALLOWED set if it is safe to send to "
        f"a maintainer, or to the EXCLUDED map with the reason it stays home."
    )


def test_the_upstream_extractors_are_not_vacuous():
    """A parse that broke must fail loudly, not pass on an empty surface.

    Every rule above is a subset check, and a subset of nothing holds. These
    counts are what stops the whole file from going green the day a model is
    renamed and the extractor stops finding it.
    """
    counts = {name: len(derive()) for name, (derive, _, _) in UPSTREAM.items()}
    assert counts["settings.json"] > 30, counts
    assert counts["hardware.json"] == 13, counts
    assert counts["multiroom Client"] >= 10, counts
    assert counts["multiroom Zone"] == 4, counts
    assert counts["EqualizerSettings"] == 7, counts
    assert counts["music library share"] == 9, counts
    assert counts["music library USB key"] == 4, counts


def test_every_exclusion_carries_a_reason():
    """An exclusion with no reason is a decision nobody can review later."""
    for name, (_, _, excluded) in UPSTREAM.items():
        for field, reason in excluded.items():
            assert isinstance(reason, str) and len(reason) > 20, (
                f"{name}.{field} is excluded without a usable reason"
            )


def test_no_admitted_journal_unit_is_one_that_logs_a_network_name():
    """The free-text whitelist is a set of units, and these three are outside it.

    NetworkManager and wpa_supplicant log the SSID of every network they touch,
    which is the one piece of the household's identity a report must not carry;
    sshd logs where people connected from and nothing about Milō. The glob is
    `milo-*`, so none of them can be admitted by accident — this pins that the
    extras added by name never grow into one.
    """
    assert wl.JOURNAL_UNIT_GLOB == "milo-*"
    for unit in wl.JOURNAL_EXTRA_UNITS:
        assert unit not in wl.JOURNAL_EXCLUDED_UNITS, unit
        assert not unit.startswith(("NetworkManager", "wpa_supplicant", "ssh")), unit
    for unit, reason in wl.JOURNAL_EXCLUDED_UNITS.items():
        assert len(reason) > 20, f"{unit} is excluded without a usable reason"


# --------------------------------------------------------------------------- #
# The enforcement
# --------------------------------------------------------------------------- #

def test_a_url_query_string_never_survives():
    """The rule that exists because of a measured leak.

    The Music Library streams from Navidrome over Subsonic token auth, so mpv's
    URL carries `u=<user>&t=<md5(password+salt)>&s=<salt>`. The producer no
    longer logs it, but errors.log keeps three rotations of the lines that did.
    """
    line = (
        "INFO Loading stream: http://127.0.0.1:4533/rest/stream"
        "?u=milo&t=6f1ed002ab5595859014ebf0951522d9&s=abcdef01"
    )
    out = wl.strip_query_strings(line)
    assert "6f1ed002ab5595859014ebf0951522d9" not in out
    assert "abcdef01" not in out
    assert "u=milo" not in out
    # The path survives, or the line stops being a diagnosis.
    assert "/rest/stream?…" in out


def test_a_global_ipv6_address_is_replaced_and_a_local_one_is_not():
    """Local addresses stay — a multiroom fault cannot be read without them.

    A global IPv6 is a different thing: its /64 comes from the ISP and names the
    subscriber line. Measured in this unit's avahi journal, which announces the
    address it registered on every start (the line below carries a 2001:db8::/32
    stand-in: the redaction reads the 2000::/3 shape, which it shares).
    Link-local and ULA are local, and are what a routing or an mDNS problem is
    actually read from.
    """
    out = wl.strip_global_ipv6(
        "Registering 2001:db8:0:1:a4c3:1eff:fe2b:9d55 on eth0; "
        "nameserver fd7a:115c:a1e0::53; peer fe80::1; host 192.168.1.55"
    )
    assert "2001:db8" not in out and "<global-ipv6>" in out
    assert "fd7a:115c:a1e0::53" in out
    assert "fe80::1" in out
    assert "192.168.1.55" in out
    # A timestamp is not an address.
    assert wl.strip_global_ipv6("2026-09-04T18:05:20+02:00 ok") == "2026-09-04T18:05:20+02:00 ok"


@pytest.mark.parametrize("line", [
    "public address is 82.64.10.23.",
    "host=82.64.10.23.",
    "checked 82.64.10.23...",
    "82.64.10.23, then the port",
    "peer_82.64.10.23_x",
    "(82.64.10.23)",
    "82.64.10.23:443",
])
def test_a_public_ipv4_address_is_replaced_wherever_it_ends(line):
    """The logs page's rule for the household's public address (an external
    check, a port forward), which names the subscriber line. A sentence's own
    period after it, its most common place, is where it escaped."""
    out = wl.strip_public_ipv4(line)
    assert "82.64.10.23" not in out and "<public-ipv4>" in out, out


def test_a_local_address_and_a_dotted_version_are_not_a_public_address():
    line = "lan 192.168.1.5. tailscale 100.64.0.7, package 1.2.3.4.5 and v8.8.8.8"
    assert wl.strip_public_ipv4(line) == line


@pytest.mark.parametrize("line, kept", [
    ("level=warn msg=\"Could not read tags\" path=/media/milo/share-1/Leo/Private/A/01.flac err=EIO",
     "path=/media/milo/share-1/<path> err=EIO"),
    ("level=warn msg=\"Could not read tags\" path=\"/media/milo/share-1/Leo/My Album/01 Song.flac\" err=EIO",
     "path=\"/media/milo/share-1/<path>\" err=EIO"),
    ("open('/media/milo/share-1/Leo/a b/c.mp3') failed", "open('/media/milo/share-1/<path>') failed"),
])
def test_a_path_under_a_mounted_share_keeps_its_mount_only(line, kept):
    """The folders and files a household keeps on a NAS or a USB drive are
    theirs; Navidrome's scanner names the file it could not read. The mount
    stays — it says which storage space the fault is on."""
    out = wl.Redactor()(line)
    assert kept in out, out
    for private in ("Leo", "Private", "Album", "Song", "a b"):
        assert private not in out, out


def test_a_usb_drive_path_is_cut_before_its_mountpoint_is_labeled():
    """A USB drive's mountpoint is replaced whole by its label: the path
    after it must be gone first, or nothing would read it any more."""
    redactor = wl.Redactor({"/media/milo/SANDISK": "usb-1"})
    out = redactor("scan error path=/media/milo/SANDISK/Leo/Private/01.flac")
    assert out == "scan error path=usb-1/<path>"


def test_the_redactor_replaces_an_excluded_value_wherever_it_appears():
    """An exclusion has to follow its value into free text, or it is decorative.

    Measured in this unit's errors.log: `Share nas-leo-d7992dfe stopped
    answering`. The id is excluded because it is generated from the name the
    user typed — and here it is, in a log line no projection touches.
    """
    redactor = wl.Redactor.build(
        client_labels={"aa:bb": "client-1"},
        client_names={"aa:bb": "Canapé"},
        zone_labels={"zone-uuid": "zone-1"},
        zone_names={"zone-uuid": "Salon"},
        share_labels={"nas-leo-d7992dfe": "share-1"},
        share_names={"nas-leo-d7992dfe": "NAS-Leo"},
        usb_strings={"iPod de Léo": "usb-1"},
        ssid_labels={"Freebox-Leo": "wifi-1"},
    )
    out = redactor(
        "Share nas-leo-d7992dfe stopped answering; zone Salon (zone-uuid) holds "
        "Canapé; mounted /media/milo/nas-leo-d7992dfe and iPod de Léo; "
        "nmcli timed out: nmcli connection show milo-Freebox-Leo"
    )
    for secret in (
        "nas-leo-d7992dfe", "Salon", "zone-uuid", "Canapé", "iPod de Léo", "Freebox-Leo",
    ):
        assert secret not in out, secret
    assert out.count("share-1") == 2
    assert "client-1" in out and "zone-1" in out and "usb-1" in out
    assert "milo-wifi-1" in out


def test_a_known_network_keeps_its_label_through_the_structural_rule():
    """The label cross-references one network across sections; `<wifi>` would not."""
    redactor = wl.Redactor({"Freebox-Leo": "wifi-1"})
    assert redactor(
        "Could not hand WiFi 'Freebox-Leo' to satellite 192.168.1.60: timeout"
    ) == "Could not hand WiFi 'wifi-1' to satellite 192.168.1.60: timeout"
    assert redactor(
        "Deleting WiFi profile: milo-Freebox-Leo (ssid=Freebox-Leo)"
    ) == "Deleting WiFi profile: milo-wifi-1 (ssid=wifi-1)"


def _ssid_redactor(ssid_labels):
    return wl.Redactor.build(
        client_labels={}, client_names={}, zone_labels={}, zone_names={},
        share_labels={}, share_names={}, usb_strings={},
        ssid_labels=ssid_labels,
    )


def test_a_known_network_is_replaced_in_a_host_name_whatever_its_case():
    """A host name is folded to lowercase before it reaches a log — the
    middleware refuses `Host: freebox-leo:8000` as the browser sent it — and
    the SSID names the household all the same. Matched on its exact case
    only, it stayed in the report under the one spelling nothing rewrote."""
    redactor = _ssid_redactor({"Freebox-Leo": "wifi-1"})
    out = redactor(
        "Refused GET /api/audio/state: untrusted Host 'freebox-leo:8000'; "
        "Refused WebSocket /ws: untrusted Origin 'http://FREEBOX-LEO.local'; "
        "resolved freebox-leo.local"
    )
    assert "freebox-leo" not in out.lower()
    assert out.count("wifi-1") == 3


def test_a_network_named_like_a_word_does_not_rewrite_the_word():
    """Over-redaction that destroys the log helps nobody either.

    An SSID is matched in another case only where a host name is written, so
    a network called "Home" leaves every `/home/milo` path of a traceback
    readable, and one called "WiFi" leaves the word wifi alone.
    """
    redactor = _ssid_redactor({"Home": "wifi-1", "WiFi": "wifi-3"})
    for line in (
        'File "/home/milo/milo/backend/main.py", line 12',
        "home network up",
        "wifi connected on wlan0",
    ):
        assert redactor(line) == line
    assert redactor("Joined Home") == "Joined wifi-1"


def test_a_label_is_never_rewritten_by_another_value():
    """Each value is replaced in one pass over the original text, so a
    network called "WiFi" cannot match inside the `wifi-2` another network
    was just replaced by — the label would no longer say which network it is."""
    redactor = _ssid_redactor({"Livebox-WiFi": "wifi-2", "WiFi": "wifi-3", "wifi": "wifi-4"})
    assert redactor("Connected to Livebox-WiFi") == "Connected to wifi-2"
    assert redactor("Joined wifi then Livebox-WiFi") == "Joined wifi-4 then wifi-2"


def test_a_network_named_like_the_appliance_does_not_rewrite_its_unit_names():
    """An SSID "milo" would turn every `milo-backend` into `wifi-1-backend`.

    It names no household, so it is the network that is left readable, not the
    unit and host names that are destroyed.
    """
    redactor = wl.Redactor.build(
        client_labels={}, client_names={}, zone_labels={}, zone_names={},
        share_labels={}, share_names={}, usb_strings={},
        ssid_labels={"milo": "wifi-1", "Milo-Client": "wifi-2"},
    )
    assert redactor("milo-backend.service started on milo-client") == (
        "milo-backend.service started on milo-client"
    )
    # Nor does the setup hotspot every Milō raises lose its name to the rule.
    line = "Access point 'Milō' is up from a previous run — adopted"
    assert redactor(line) == line


def test_a_label_does_not_turn_its_line_into_a_network_line():
    """Only a line about a network has its quoted values taken; the `wifi-N`
    the redactor wrote into it is not what makes it one."""
    redactor = wl.Redactor({"Home": "wifi-1"})
    assert redactor("connection up milo-Home failed: 'Secrets were required'") == (
        "connection up milo-wifi-1 failed: 'Secrets were required'"
    )


def test_a_short_value_is_not_substituted_into_unrelated_words():
    """Over-redaction that destroys the log helps nobody either.

    A room named "Ho" would rewrite the middle of every word containing it, so
    values under three characters are skipped and the rest match on a word
    boundary — `Salonique` is not the zone `Salon`.
    """
    redactor = wl.Redactor({"Ho": "client-1", "Salon": "zone-1"})
    assert redactor("Ho and Salonique and Salon") == "Ho and Salonique and zone-1"


# Every logger call in the two applications that hands a network name to its
# format string. The report reads both: the server's own errors.log and journal,
# and each satellite's journal inside its block.
LOGGED_TREES = (REPO_ROOT / "backend", REPO_ROOT / "milo-client" / "app")
_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}
_SPEC = re.compile(r"%(?:\([^)]*\))?[#0\- +]*\d*(?:\.\d+)?([a-zA-Z%])")


def _logged_ssid_formats():
    """(where, format, which argument indexes carry an SSID), read from source."""
    found = []
    for tree_root in LOGGED_TREES:
        for path in sorted(tree_root.rglob("*.py")):
            if "tests" in path.relative_to(REPO_ROOT).parts:
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _LOG_METHODS
                    and ast.unparse(node.func.value).endswith("logger")
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    continue
                fmt = node.args[0].value
                carriers = {}
                for index, arg in enumerate(node.args[1:]):
                    source = ast.unparse(arg)
                    if "ssid" in source.lower():
                        carriers[index] = "{ssid}"
                    elif source in _PROFILE_ARGS and _PROFILE_LINE.search(fmt):
                        carriers[index] = "milo-{ssid}"
                    elif source in _NMCLI_ARGV and fmt.startswith("nmcli "):
                        carriers[index] = (
                            "connection add type wifi con-name milo-{ssid} "
                            "ifname wlan0 ssid {ssid}"
                        )
                if carriers:
                    where = f"{path.relative_to(REPO_ROOT)}:{node.lineno}"
                    found.append((where, fmt, carriers))
    return found


# A profile name is `milo-<ssid>` (or the SSID itself), and an
# nmcli argv carries both: they name the network under another variable.
_PROFILE_ARGS = {"name", "active_name", "profile", "con_name"}
_PROFILE_LINE = re.compile(r"profile|connection|credentials", re.IGNORECASE)
_NMCLI_ARGV = {"_redact_nmcli_args(args)", "' '.join(args)"}


def _render(fmt: str, carriers, ssid: str) -> str:
    """The message as logging would write it, the SSID in its own slots."""
    values, index = [], 0
    for match in _SPEC.finditer(fmt):
        conversion = match.group(1)
        if conversion == "%":
            continue
        if index in carriers:
            values.append(carriers[index].format(ssid=ssid))
        else:
            values.append(0 if conversion in "di" else "x")
        index += 1
    return fmt % tuple(values)


def test_the_logged_ssid_extractor_is_not_vacuous():
    """The guard below is a for-all, and a for-all over nothing holds."""
    found = _logged_ssid_formats()
    assert len(found) >= 20, found
    assert any(where.startswith("milo-client/") for where, _, _ in found), found
    # The two nmcli timeout lines, which name a profile under neither spelling.
    assert sum("nmcli timed out" in fmt for _, fmt, _ in found) == 2, found


@pytest.mark.parametrize("ssid", ["Sentinel Net Name", "Léo's (5 GHz)"])
def test_every_logged_ssid_shape_is_redacted_without_knowing_it(ssid):
    """A network forgotten since is in no NetworkManager profile, so no known
    value can redact it — yet errors.log's rotations and the journal still hold
    the lines that named it. What removes it is the rule on the shapes Milō's
    own lines give a name, and this goes red the day a logger call writes one in
    a shape the rule does not read.
    """
    redact = wl.Redactor()
    leaks = []
    for where, fmt, carriers in _logged_ssid_formats():
        message = _render(fmt, carriers, ssid)
        for line in (
            f"[2026-09-04 16:56:13,146] WARNING backend.x - {message}",
            f"2026-09-04T16:56:13+02:00 milo python[812]: INFO backend.x - {message}",
        ):
            if ssid in redact(line):
                leaks.append(f"{where}: {redact(line)}")
    assert not leaks, "\n".join(leaks)


# --------------------------------------------------------------------------- #
# Devices and listening — what the household plays, and from what
# --------------------------------------------------------------------------- #

def _text_leaves(annotation, path):
    """Every text leaf under `annotation`, by the path the wire spells it.

    A closed vocabulary (an enum, a Literal) names nobody, and neither does a
    number. A union of models is the discriminated `details`, each member under
    its own `kind`. A block passed on as its producer built it (`Dict[str,
    Any]`) is the leaf `<path>.*`: no type says what it holds, so it has to be
    decided like any field. Anything else raises: an annotation this walk does
    not read would otherwise be a field it skips in silence.
    """
    origin, args = typing.get_origin(annotation), typing.get_args(annotation)
    if origin is typing.Annotated:
        yield from _text_leaves(args[0], path)
    elif origin in (typing.Union, types.UnionType):
        members = [arg for arg in args if arg is not type(None)]
        if len(members) > 1:
            for member in members:
                kind = member.model_fields["kind"].default
                yield from _model_leaves(member, f"{path}.{kind}")
        else:
            yield from _text_leaves(members[0], path)
    elif origin is list:
        yield from _text_leaves(args[0], f"{path}[]")
    elif origin is dict or annotation is typing.Any:
        yield f"{path}.*"
    elif origin is typing.Literal or annotation in (int, float, bool):
        return
    elif isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return
    elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield from _model_leaves(annotation, path)
    elif annotation is str:
        yield path
    else:
        raise TypeError(f"{path}: no rule reads a {annotation!r}")


def _model_leaves(model, prefix=""):
    for name, field in model.model_fields.items():
        yield from _text_leaves(field.annotation, f"{prefix}.{name}" if prefix else name)


def _under_an_opaque_block(path, leaves):
    """Whether `path` is a key inside a `<block>.*` leaf, which no type lists."""
    return any(
        leaf.endswith(".*") and path.startswith(leaf[:-1]) for leaf in leaves
    )


def test_every_text_of_the_audio_state_is_decided():
    """Goes red when the state gains a text field and nobody said whether it
    names the household.

    The Redactor is taught the state's titles, artists and senders so that the
    same value is labeled wherever a log carries it. A field the state gains
    and these two halves do not name is a field the Redactor never learns: a
    daemon's journal would print it, untouched. A block no type describes (the
    Subsonic tracks of the Music Library's queue, the podcast episode) is a
    `.*` leaf, kept as a whole in STATE_NOT_REDACTED with its reason, and the
    keys of it that name what plays are listed in STATE_REDACTED.
    """
    leaves = frozenset(_model_leaves(AudioState))
    assert len(leaves) >= 40, sorted(leaves)
    for leaf in (
        "details.spotify.remote.device_name",
        "details.music_library.queue[].*",
        "details.podcast.episode.*",
    ):
        assert leaf in leaves, sorted(leaves)

    undecided = sorted(
        leaf for leaf in leaves
        if leaf not in wl.STATE_REDACTED and leaf not in wl.STATE_NOT_REDACTED
    )
    assert not undecided, (
        f"the audio state carries text the report has not decided about: {undecided}. "
        "Add each one to STATE_REDACTED with its label kind if it names what is "
        "played or who sends it, or to STATE_NOT_REDACTED with the reason it does not."
    )
    assert not set(wl.STATE_REDACTED) & set(wl.STATE_NOT_REDACTED)
    assert not {
        path for path in (*wl.STATE_REDACTED, *wl.STATE_NOT_REDACTED)
        if path not in leaves and not _under_an_opaque_block(path, leaves)
    }, "a decision names a path the state no longer has"
    assert not {leaf for leaf in leaves if leaf.endswith(".*")} & set(wl.STATE_REDACTED), (
        "a whole opaque block is not one value: name the keys of it that are"
    )
    for path, reason in wl.STATE_NOT_REDACTED.items():
        assert len(reason) > 20, f"{path} is not redacted without a usable reason"


def test_the_state_values_are_read_where_the_wire_puts_them():
    """The walk names a path exactly as the decision above spells it, a
    `details` block under its kind, a list item under `[]`, and a key inside a
    block no type describes by the producer's own spelling."""
    state = {
        "source": "spotify",
        "session": {"title": "A Song", "artist": "A Band", "senders": ["iPhone de Léo"]},
        "details": {
            "kind": "spotify",
            "account": "leo",
            "artists": [{"name": "A Band", "uri": "spotify:artist:1"}],
            "remote": {"device_name": "Pixel de Léa", "device_type": "smartphone"},
        },
    }
    assert wl.state_values(state) == [
        ("title", "A Song"),
        ("artist", "A Band"),
        ("sender", "iPhone de Léo"),
        ("account", "leo"),
        ("artist", "A Band"),
        ("sender", "Pixel de Léa"),
    ]
    library = {
        "details": {
            "kind": "music_library",
            "queue": [
                {"id": "t1", "title": "Earlier", "artist": "Band", "album": "LP", "suffix": "flac"},
                {"id": "t2", "title": "Now", "artist": "Band", "album": "LP", "suffix": "flac"},
            ],
            "track_id": "t2",
        },
    }
    assert wl.state_values(library) == [
        ("title", "Earlier"), ("artist", "Band"), ("album", "LP"),
        ("title", "Now"), ("artist", "Band"), ("album", "LP"),
    ]
    podcast = {
        "details": {
            "kind": "podcast",
            "episode": {"uuid": "e1", "name": "Episode", "podcast": {"uuid": "p1", "name": "Show"}},
        },
    }
    assert wl.state_values(podcast) == [("title", "Episode"), ("artist", "Show")]


def _build(**known):
    """`Redactor.build` for a unit that holds only `known`: no client, zone,
    share, USB storage or network otherwise."""
    nothing = dict.fromkeys((
        "client_labels", "client_names", "zone_labels", "zone_names",
        "share_labels", "share_names", "usb_strings", "ssid_labels",
    ), {})
    return wl.Redactor.build(**{**nothing, **known})


def test_a_number_from_the_state_does_not_rewrite_the_logs():
    """An album called "1989" or a track called "404" names nobody, and taught
    as a whole word it would turn every HTTP status, year and port in the logs
    into a label."""
    redact = _build(listening=[("album", "1989"), ("title", "404"), ("title", "Parapluie")])
    assert redact("Navidrome answered 404 for Parapluie (1989)") == (
        "Navidrome answered 404 for title-1 (1989)"
    )


RADIO_WIRE = REPO_ROOT / "backend" / "tests" / "golden" / "wire" / "radio.json"


def _recorded_radio_states():
    """Every state the radio source was recorded publishing with a station on
    it, over the WebSocket and over REST alike."""
    recorded = json.loads(RADIO_WIRE.read_text())
    states = [
        *(
            event["data"] for run in recorded.values() for event in run["ws"]
            if (event["category"], event["type"]) == ("source", "state")
        ),
        *(state for run in recorded.values() for state in run["rest"]),
    ]
    return [state for state in states if (state["details"] or {}).get("station")]


def test_a_station_the_report_keeps_is_not_taught_from_the_session():
    """Goes red when a radio station's name is labeled after all.

    The station is kept on purpose, because a radio fault is read from it, yet
    radio writes it on paths the Redactor is taught: as the session's album,
    as its title while no song is recognized, and as the resume point's title
    and album while stopped. Read from the states the radio source was
    recorded publishing (tests/golden/wire), so the shape is the producer's
    own; the song recognized in the stream is still labeled.
    """
    states = _recorded_radio_states()
    assert len(states) >= 10, states
    songs = 0
    for state in states:
        redact = _build(listening=wl.state_values(state))
        station = state["details"]["station"]["name"]
        # sources/radio/source.py and browser_api.py, as the journal writes them.
        for line in (
            f"INFO:source.radio:Playing station: {station}",
            f"DEBUG:source.radio.browser_api:Fetched station 96: {station}",
        ):
            assert redact(line) == line, state
        song = state["details"]["track"]
        if song:
            songs += 1
            out = redact(f"milo mpv[700]: icy-title: {song['title']} on {station}")
            assert song["title"] not in out and out.endswith(f" on {station}"), out
    assert songs >= 2, "no recorded state recognizes a song any more"


# One sentinel per kind a logger call can carry. With a space and an accent, so
# a shape is proved to take a whole name.
LISTENING_SENTINELS = {
    "title": "Sentinel Sóng Title",
    "artist": "Sentinel Bánd",
    "album": "Sentinel Récord",
    "device": "Sentinel Phóne",
    "filter": "SENTINELFILTER",
}
BT_SENTINEL = "4C:2E:5E:7A:01:9B"
# The Spotify account go-librespot names, which no Milō line carries.
ACCOUNT_SENTINEL = "sentinelaccount"
_SENTINEL_OF = {
    **LISTENING_SENTINELS,
    "track": f"{LISTENING_SENTINELS['artist']} – {LISTENING_SENTINELS['title']}",
    "address": BT_SENTINEL,
    "path": f"/org/bluez/hci0/dev_{BT_SENTINEL.replace(':', '_')}",
}

# What an argument carries, read off its expression — in order, the first match
# decides. A bare `name` is a device, an artist or a title according to the
# line it is written into.
_CARRIERS = (
    (r"^len\(", None, None),
    (r"_named\(|^named$", None, "track"),
    (r"\bdevice_name_filter$", None, "filter"),
    (r"(?:^|\.)(?:q_)?title$|\.get\('title'\)|^episode\.get\('name'", None, "title"),
    (r"(?:^|\.)artist$|^candidate$|^artist\.get\('name'\)", None, "artist"),
    (r"(?:^|\.)album$", None, "album"),
    (r"^name$", r"Deezer|artist", "artist"),
    (r"^name$", r"Cover", "title"),
    (r"^request\.name$", r"playlist", "title"),
    (r"^(?:device_name|device\.name|session\.name)$", None, "device"),
    (r"^name$", r"[Dd]evice|bond|[Cc]onnect|answers no ping", "device"),
)
# In the Bluetooth modules alone, an address is a Bluetooth address and a
# `device` is BlueZ's object path for one.
_BLUETOOTH_CARRIERS = (
    (r"^(?:address|session\.sender|device\.uniq)$", "address"),
    (r"^device$", "path"),
)
# Modules whose `title` is a section of the report, not a track's.
_NOT_A_TRACK_TITLE = ("backend/core/system/diagnostic/", "milo-client/app/routes/diagnostic.py")


def _carrier(source: str, fmt: str, where: str) -> typing.Optional[str]:
    module = where.rsplit(":", 1)[0]
    bluetooth = "/bluetooth/" in module or module.endswith("/bt_remote.py")
    if bluetooth:
        for pattern, kind in _BLUETOOTH_CARRIERS:
            if re.search(pattern, source):
                return kind
    for pattern, line, kind in _CARRIERS:
        if re.search(pattern, source) and (line is None or re.search(line, fmt)):
            if kind == "title" and where.startswith(_NOT_A_TRACK_TITLE):
                return None
            # A network's profile is the SSID guard's, above.
            if kind == "device" and re.search(r"(?i)wi-?fi", fmt):
                return None
            return kind
    return None


def _placeholder(source: str, conversion: str):
    """What a slot that carries nothing holds: an address where one is
    written (a Mac named before its IP), a number or a word otherwise."""
    if re.search(r"\bip\b|address", source):
        return "192.168.1.20"
    return 0 if conversion in "dixXoeEfFgG" else "x"


def _logged_listening_lines():
    """(where, logger, message) for every logger call that names a device or
    what plays, the carrier slots holding their sentinels — read from source."""
    found = []
    for tree_root in LOGGED_TREES:
        for path in sorted(tree_root.rglob("*.py")):
            relative = path.relative_to(REPO_ROOT)
            if "tests" in relative.parts:
                continue
            module = ".".join(relative.with_suffix("").parts)
            for node in ast.walk(ast.parse(path.read_text())):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _LOG_METHODS
                    and ast.unparse(node.func.value).endswith("logger")
                    and node.args
                ):
                    continue
                where = f"{relative}:{node.lineno}"
                first = node.args[0]
                if isinstance(first, ast.JoinedStr):
                    fmt = ast.unparse(first)
                    slots = [
                        (value, _carrier(ast.unparse(value.value), fmt, where))
                        for value in first.values if isinstance(value, ast.FormattedValue)
                    ]
                    if not any(kind for _, kind in slots):
                        continue
                    kinds = iter(slots)
                    parts = []
                    for value in first.values:
                        if isinstance(value, ast.Constant):
                            parts.append(str(value.value))
                            continue
                        _, kind = next(kinds)
                        if kind is None:
                            parts.append(str(_placeholder(ast.unparse(value.value), "s")))
                        else:
                            sentinel = _SENTINEL_OF[kind]
                            parts.append(repr(sentinel) if value.conversion == ord("r") else sentinel)
                    message = "".join(parts)
                elif isinstance(first, ast.Constant) and isinstance(first.value, str):
                    fmt = first.value
                    conversions = [
                        m.group(1) for m in _SPEC.finditer(fmt) if m.group(1) != "%"
                    ]
                    sources = [ast.unparse(arg) for arg in node.args[1:]]
                    sources += [""] * (len(conversions) - len(sources))
                    kinds = [_carrier(source, fmt, where) for source in sources]
                    if not any(kinds):
                        continue
                    message = fmt % tuple(
                        _placeholder(source, conversion) if kind is None else _SENTINEL_OF[kind]
                        for source, kind, conversion in zip(sources, kinds, conversions)
                    )
                else:
                    continue
                found.append((where, module, message))
    return found


def test_the_logged_listening_extractor_is_not_vacuous():
    """The guard below is a for-all, and a for-all over nothing holds — so the
    producers it exists for are named, one per kind of value."""
    found = _logged_listening_lines()
    assert len(found) >= 40, found
    places = {where.rsplit(":", 1)[0] for where, _, _ in found}
    for producer in (
        "backend/sources/bluetooth/source.py",
        "backend/sources/bluetooth/monitor.py",
        "backend/sources/bluetooth/adapter.py",
        "backend/sources/bluetooth/agent.py",
        "backend/hardware/bt_remote.py",
        "backend/sources/mac/source.py",
        "backend/core/mac_link/calibration_service.py",
        "backend/shared/artwork_resolver.py",
        "backend/core/lyrics/service.py",
        "backend/sources/cd/source.py",
        "backend/sources/podcast/source.py",
        "backend/sources/radio/shazam.py",
        "backend/sources/music_library/artist_images.py",
    ):
        assert producer in places, f"{producer} names no device or track any more?"
    for sentinel in (*LISTENING_SENTINELS.values(), BT_SENTINEL, _SENTINEL_OF["path"]):
        assert any(sentinel in message for _, _, message in found), sentinel


def _leaked(text: str):
    return [
        sentinel for sentinel in (
            *LISTENING_SENTINELS.values(), ACCOUNT_SENTINEL, BT_SENTINEL, BT_SENTINEL.lower()
        )
        if sentinel in text
    ] + (["the address, path form"] if BT_SENTINEL.replace(":", "_") in text else [])


def test_every_logged_listening_shape_is_redacted_without_knowing_it():
    """The track played three hours ago is in no state the Redactor can learn
    from, yet the journal and errors.log's rotations still hold the lines that
    named it. What removes it is the rule on the shapes Milō's own lines give a
    name, and this goes red the day a logger call writes one in a shape the
    rule does not read.

    Each line is written the way the logs the report reads would write it: a
    backend line as the server's journal and its errors.log do, a satellite's
    line as its own journal does.
    """
    leaks = []
    for where, logger_name, message in _logged_listening_lines():
        if where.startswith("milo-client/"):
            lines = (
                f"2026-09-04T16:56:13+00:00 milo-client python[611]: "
                f"2026-09-04 16:56:13,146 - {logger_name} - INFO - {message}",
            )
        else:
            lines = (
                f"2026-09-04T16:56:13+02:00 milo python3[812]: INFO:{logger_name}:{message}",
                f"[2026-09-04 16:56:13,146] WARNING {logger_name} - {message}",
            )
        for line in lines:
            out = wl.Redactor()(line)
            if _leaked(out):
                leaks.append(f"{where}: {out}")
    assert not leaks, "\n".join(leaks)


# The daemons' own lines, which no source file declares. go-librespot's are
# measured (tests/test_spotify_*.py hold the same lines); bluetoothd's and
# BlueALSA's are the shapes they write an address in; Navidrome's is read from
# its scrobble handler's source, not yet from a unit.
DAEMON_LINES = (
    "milo go-librespot[920]: level=info msg=\"loaded track \\\"Sentinel Sóng Title\\\" "
    "(paused: false, position: 0ms, duration: 215000ms, prefetched: false)\"",
    "milo go-librespot[920]: level=info msg=\"loaded episode \\\"Sentinel Sóng Title\\\" "
    "(paused: false, position: 0ms, duration: 3600000ms, prefetched: false)\"",
    "milo go-librespot[920]: level=info msg=\"accepted zeroconf from Sentinel Phóne\" "
    f"username=\"{ACCOUNT_SENTINEL}\"",
    "milo go-librespot[920]: level=info msg=\"refused zeroconf from Sentinel Phóne\" "
    f"username=\"{ACCOUNT_SENTINEL}\"",
    "milo go-librespot[920]: level=error msg=\"failed creating new session from Sentinel Phóne\" "
    "error=\"failed authenticating with login5: TRY_AGAIN_LATER\"",
    f"milo go-librespot[920]: level=info msg=\"authenticated AP\" username=\"{ACCOUNT_SENTINEL}\"",
    "milo navidrome[530]: time=\"2026-09-04T16:56:13+02:00\" level=info msg=\"Now Playing\" "
    "artist=\"Sentinel Bánd\" player=\"milo [Milo]\" title=\"Sentinel Sóng Title\" user=milo",
    f"milo bluetoothd[402]: src/service.c:btd_service_connect() a2dp-sink profile "
    f"connect failed for {BT_SENTINEL}: Protocol not available",
    f"milo bluealsa[401]: Couldn't open transport /org/bluealsa/hci0/dev_"
    f"{BT_SENTINEL.replace(':', '_')}/a2dpsnk/source: Device busy",
)


@pytest.mark.parametrize("line", DAEMON_LINES)
def test_a_daemon_naming_a_device_or_a_track_is_redacted_without_knowing_it(line):
    out = wl.Redactor()(f"2026-09-04T16:56:13+02:00 {line}")
    assert not _leaked(out), out
    # Only the value goes: the line still says what happened.
    assert out.split(": ", 1)[1][:24] == line.split(": ", 1)[1][:24]


@pytest.mark.parametrize("line", [line for line in DAEMON_LINES if "go-librespot" in line])
def test_a_daemon_line_the_spotify_source_relays_is_redacted_too(line):
    """The Spotify source relays go-librespot's failures at error, its msg and
    its error joined, so the same name reaches the backend's journal, errors.log
    and the previous boot in a shape no daemon writes — and a refused Connect is
    exactly when a report gets generated. Rendered by the source's own
    extraction, as each of those logs writes the record."""
    message = SpotifySource._extract_log_message(None, line)
    for relayed in (
        f"2026-09-04T16:56:13+02:00 milo python3[812]: ERROR:source.spotify:{message}",
        f"[2026-09-04 16:56:13,146] ERROR source.spotify - {message}",
    ):
        out = wl.Redactor()(relayed)
        assert not _leaked(out), out


def test_a_logfmt_field_is_taken_whole_and_an_absent_one_is_left():
    """logrus quotes a value only when it needs to, and a Python repr writes
    it single-quoted: each is taken whole. A field that holds nothing keeps its
    word, so `title=None` does not become a title. Every field the rule reads
    by name is written here, and an artist met again under `albumArtist` is the
    same artist."""
    redact = wl.Redactor()
    assert redact(
        'milo navidrome[530]: level=info msg="Now Playing" artist="Daft Punk" title=Aerodynamic user=milo'
    ) == 'milo navidrome[530]: level=info msg="Now Playing" artist="artist-1" title=title-1 user=milo'
    assert redact("DEBUG:source.cd:Track(title='One More Time', artist=None)") == (
        "DEBUG:source.cd:Track(title='title-2', artist=None)"
    )
    assert redact(
        'level=debug msg=scrobble album="Random Access Memories" albumArtist="Daft Punk" year=2013'
    ) == 'level=debug msg=scrobble album="album-1" albumArtist="artist-1" year=2013'
    assert redact("DEBUG:source.cd:Disc(album=Discovery, album_id=None)") == (
        "DEBUG:source.cd:Disc(album=album-2, album_id=None)"
    )


def test_one_device_is_one_label_wherever_a_line_names_it():
    """A label is only worth something if it cross-references: the phone that
    connected, the address bluetoothd refused and the session that ended are
    one device, and a second phone is another."""
    redact = wl.Redactor()
    lines = [
        redact(f"INFO:source.bluetooth:Device connected: iPhone de Léo ({BT_SENTINEL})"),
        redact(f"milo bluetoothd[402]: a2dp-sink profile connect failed for {BT_SENTINEL}: busy"),
        redact("INFO:source.bluetooth:milo-bluealsa.service is stopping — ending iPhone de Léo's session"),
        redact("INFO:source.bluetooth:Device connected: Pixel (11:22:33:44:55:66)"),
    ]
    assert lines == [
        "INFO:source.bluetooth:Device connected: bt-1 (bt-1)",
        "milo bluetoothd[402]: a2dp-sink profile connect failed for bt-1: busy",
        "INFO:source.bluetooth:milo-bluealsa.service is stopping — ending bt-1's session",
        "INFO:source.bluetooth:Device connected: bt-2 (bt-2)",
    ]


def test_a_device_named_before_its_address_keeps_its_label():
    """errors.log, the journal and the previous boot are sanitized in whatever
    order their reads complete, so the line naming a phone alone can come
    first: the address that follows is the same device, not a second one."""
    redact = wl.Redactor()
    lines = [
        redact("INFO:source.bluetooth:milo-bluealsa.service is stopping — ending iPhone de Léo's session"),
        redact(f"INFO:source.bluetooth:Device connected: iPhone de Léo ({BT_SENTINEL})"),
        redact(f"milo bluetoothd[402]: a2dp-sink profile connect failed for {BT_SENTINEL}: busy"),
    ]
    assert lines == [
        "INFO:source.bluetooth:milo-bluealsa.service is stopping — ending bt-1's session",
        "INFO:source.bluetooth:Device connected: bt-1 (bt-1)",
        "milo bluetoothd[402]: a2dp-sink profile connect failed for bt-1: busy",
    ]


def test_a_device_the_unit_knows_is_labeled_wherever_it_appears():
    """A device BlueZ holds is labeled in address order before the report
    starts, so its name is caught in a line no shape reads — and the state's
    sender for it is that same device, not a second label."""
    redact = _build(
        bt_devices={"F0:00:00:00:00:02": "Pixel de Léa", "A0:00:00:00:00:01": "iPhone de Léo"},
        listening=[("sender", "iPhone de Léo"), ("title", "Parapluie"), ("artist", "Tiakola")],
    )
    assert redact("shairport-sync: Connection from iPhone de Léo, playing Parapluie by Tiakola") == (
        "shairport-sync: Connection from bt-1, playing title-1 by artist-1"
    )
    assert redact("milo bluetoothd[402]: refused f0:00:00:00:00:02") == "milo bluetoothd[402]: refused bt-2"


def test_a_multiroom_address_is_never_taken_for_a_bluetooth_one():
    """Over-redaction that destroys the log helps nobody either. A multiroom
    client's MAC is what a multiroom fault is read from, so it survives even on
    a Bluetooth line; and a MAC on any other line is left alone, because the
    one written there is a snapclient's."""
    redact = wl.Redactor(keep_macs={"DE:AD:BE:EF:00:01"})
    for line in (
        "milo bluetoothd[402]: de:ad:be:ef:00:01 is a speaker, not a phone",
        "milo snapserver[510]: Hello from 11:22:33:44:55:66, host milo-client",
        "INFO:backend.core.multiroom.client_registry:Client added: 11:22:33:44:55:66 (client-2)",
        "2026-09-04T16:56:13+02:00 milo kernel: hci0: unexpected event 0x0e",
    ):
        assert redact(line) == line


def test_a_client_bluez_also_lists_stays_a_multiroom_client():
    """The report builds its Redactor from the registry and from BlueZ at
    once, and that is the path the multiroom MACs have to survive, not only
    the constructor: a client's address is kept on a Bluetooth line too, with
    the registry's lowercase spelling and BlueZ's uppercase one, and takes no
    `bt-N` from the phones."""
    speaker, phone = "DE:AD:BE:EF:00:01", "F0:00:00:00:00:02"
    redact = _build(
        client_labels={speaker.lower(): "client-1"},
        bt_devices={speaker: "Living room speaker", phone: "iPhone de Léo"},
    )
    line = f"milo bluetoothd[402]: a2dp-sink profile connect failed for {speaker}: busy"
    assert redact(line) == line
    assert redact(f"milo bluetoothd[402]: a2dp-sink profile connect failed for {phone}: busy") == (
        "milo bluetoothd[402]: a2dp-sink profile connect failed for bt-1: busy"
    )


def test_the_appliance_name_is_never_a_label():
    """A speaker paired back to its server shows in BlueZ as `milo`, and an
    AirPlay sender or an account can be called that too. Taught as a whole
    word, the name would turn the unit's own hostname, every `milo-backend`
    and every `milo.local` of the report into a label."""
    redact = _build(
        bt_devices={"A0:00:00:00:00:01": "milo", "A0:00:00:00:00:02": "milo-client"},
        listening=[("sender", "milo"), ("account", "milo-client-2")],
    )
    for line in (
        "2026-09-04T16:56:13+02:00 milo systemd[1]: Started milo-backend.service.",
        "INFO:backend.core.multiroom.satellite_update:milo-client-2 answered from milo.local",
    ):
        assert redact(line) == line


def test_a_value_the_unit_knows_keeps_its_label_inside_a_shape():
    """The values the unit knows are replaced first and the shapes read the
    line after, so a shape meets a value already rewritten. It must leave it:
    the track the state names and the one go-librespot loaded are one track,
    not `title-1` and `title-2`."""
    redact = _build(listening=[("title", "Parapluie")])
    assert redact(
        'milo go-librespot[920]: level=info msg="loaded track \\"Parapluie\\" (paused: false)"'
    ) == 'milo go-librespot[920]: level=info msg="loaded track \\"title-1\\" (paused: false)"'


@pytest.mark.asyncio
async def test_the_devices_bluez_holds_are_read_from_their_rows(monkeypatch):
    """What `bluetoothctl devices` prints beside its rows (agent chatter, a
    change notice, the controller, a row with no name) is not a device, and a
    name is taken whole, spaces included. The Redactor labels exactly what
    this returns, so a row lost here is a phone named in clear."""
    from backend.core.system.diagnostic import probes

    # The noise lines are the ones test_bt_remote.py's parse guards hold.
    output = (
        "Agent registered\n"
        "[CHG] Controller DC:A6:32:7E:D3:43 Pairable: yes\n"
        "[NEW] Device 4C:2E:5E:7A:01:9B iPhone de Léo\n"
        "Device 4C:2E:5E:7A:01:9B iPhone de Léo\n"
        "[CHG] Device 4C:2E:5E:7A:01:9B RSSI: -60\n"
        "Device f0:99:b6:12:34:56 ANTICATER VK-01\n"
        "Device AA:BB:CC:DD:EE:FF\n"
        "Device not-a-mac Pixel 7\n"
        "Controller DC:A6:32:7E:D3:43 milo [default]\n"
    )

    async def run(args, timeout=probes.DEFAULT_TIMEOUT):
        return output if args[0] == "bluetoothctl" else None

    monkeypatch.setattr(probes, "run", run)
    assert await probes.bluetooth_devices() == {
        "4C:2E:5E:7A:01:9B": "iPhone de Léo",
        "F0:99:B6:12:34:56": "ANTICATER VK-01",
    }


@pytest.mark.asyncio
async def test_a_bluetooth_stack_that_does_not_answer_costs_only_the_labels(monkeypatch):
    """`run` answers None when bluetoothctl fails, is missing or outlives its
    bound; the report then has no device to learn, and still a report."""
    from backend.core.system.diagnostic import probes

    async def run(args, timeout=probes.DEFAULT_TIMEOUT):
        return None

    monkeypatch.setattr(probes, "run", run)
    assert await probes.bluetooth_devices() == {}


def test_a_line_that_names_nothing_keeps_its_words():
    """A shape takes a value, never the sentence a status is written in."""
    redact = wl.Redactor()
    for line in (
        "INFO:source.radio.shazam:Shazam: title cleared (no verdict for 90 s)",
        "INFO:source.radio.shazam:Shazam: nothing",
        "INFO:source.radio.radiofrance:Radio France: no song",
        "INFO:source.podcast:Episode found: Unknown",
        # A Mac nothing answered for is shown by its address, which stays.
        "INFO:source.mac:Connected: 192.168.1.20 (192.168.1.20)",
    ):
        assert redact(line) == line


# --------------------------------------------------------------------------- #
# The enforcement, end to end
# --------------------------------------------------------------------------- #
# Every excluded field of every source holds a sentinel; every allowed field
# holds one too. A real report is generated over that fixture, and the two lists
# have to come out on opposite sides. The allowed half is not decoration: a
# broken extractor produces an empty report, which would satisfy the excluded
# half on its own.

EXCLUDED_SENTINELS = {
    "share id": "sentinelshareid",
    "share name": "SentinelShareName",
    "share path": "sentinelsharepath",
    "share username": "SentinelUser",
    "share domain": "SENTINELDOMAIN",
    "usb name": "SentinelUsbName",
    "usb label": "Sentinel_Usb_Label",
    "usb mountpoint": "/media/milo/Sentinel_Usb_Label",
    "client name": "SentinelRoom",
    "zone name": "SentinelZone",
    "zone id": "sentinel-zone-uuid",
    "bt remote filter": "SENTINELREMOTE",
    # With a space, so the unquoted shapes are proved to take the whole name.
    "wifi ssid": "Sentinel Net Name",
    # A network forgotten since: NetworkManager no longer names it, the journal
    # and errors.log's rotations still do.
    "forgotten wifi ssid": "Sentinel Old Net",
    # A phone BlueZ holds, by its name and its address.
    "bluetooth device name": "Sentinel Phone de Léo",
    "bluetooth address": "4C:2E:5E:7A:01:9B",
    # The AirPlay sender on air, and what it plays, as the state names them.
    "airplay sender": "Sentinel AirPlay Mac",
    "title playing": "Sentinel Now Playing",
    "artist playing": "Sentinel Now Artist",
    # Known to nothing any more: only the line that names them does.
    "spotify sender": "Sentinel Spotify Phone",
    "title played earlier": "Sentinel Earlier Song",
    # The account go-librespot names on its zeroconf lines.
    "spotify account": "sentinelspotifyaccount",
}

ALLOWED_SENTINELS = {
    "client mac": "de:ad:be:ef:00:01",
    "client ip": "192.168.55.66",
    "share host": "192.168.55.77",
}

# A line of a satellite's own block that names nothing, so the counter-proof can
# tell a sanitized block from a missing one.
SATELLITE_ALLOWED_LINE = "snapclient: latency 42 ms, buffer 1000 ms"


@pytest.fixture
def planted(monkeypatch, tmp_path):
    """A unit whose every excluded field carries a unique, greppable value."""
    from backend.core.system.diagnostic import collectors, probes, satellite
    from backend.core.system.diagnostic import service as service_module

    data_file = tmp_path / "music_library_data.json"
    data_file.write_text(json.dumps({
        "shares": [{
            "id": EXCLUDED_SENTINELS["share id"],
            "type": "cifs",
            "host": ALLOWED_SENTINELS["share host"],
            "path": EXCLUDED_SENTINELS["share path"],
            "name": EXCLUDED_SENTINELS["share name"],
            "has_credentials": True,
            "username": EXCLUDED_SENTINELS["share username"],
            "domain": EXCLUDED_SENTINELS["share domain"],
            "created_at": 1784551861,
        }],
        "known_usb": {
            "UUID-1": {
                "name": EXCLUDED_SENTINELS["usb name"],
                "label": EXCLUDED_SENTINELS["usb label"],
                "mountpoint": EXCLUDED_SENTINELS["usb mountpoint"],
                "last_seen": 1788360568,
            }
        },
    }))

    # The same values as they turn up in free text, which is where a projection
    # cannot reach them — this is the shape measured in this unit's errors.log.
    errors_log = tmp_path / "errors.log"
    errors_log.write_text(
        f"[2026-09-04 16:56:13,146] WARNING source.music_library.shares - Share "
        f"{EXCLUDED_SENTINELS['share id']} stopped answering\n"
        f"[2026-09-04 16:56:14,000] WARNING backend.core.volume - "
        f"{EXCLUDED_SENTINELS['client name']} in zone "
        f"{EXCLUDED_SENTINELS['zone name']} refused a level\n"
        f"[2026-09-04 16:56:15,000] INFO backend.shared.mpv - Loading stream: "
        f"http://127.0.0.1:4533/rest/stream?u=milo&t=deadbeefsentineltoken&s=abc\n"
        f"[2026-09-04 16:56:16,000] WARNING source.music_library - mounted "
        f"{EXCLUDED_SENTINELS['usb mountpoint']}\n"
        f"[2026-09-04 16:56:17,000] WARNING backend.core.multiroom.satellite_wifi - "
        f"Could not hand WiFi '{EXCLUDED_SENTINELS['wifi ssid']}' to satellite "
        f"{ALLOWED_SENTINELS['client ip']}: TimeoutError\n"
        # The same refusal as the Spotify source relays it.
        f"[2026-09-04 16:56:18,000] ERROR source.spotify - failed creating new session "
        f"from {EXCLUDED_SENTINELS['spotify sender']}: failed authenticating with login5: "
        "TRY_AGAIN_LATER\n"
    )

    monkeypatch.setattr(service_module, "MUSIC_LIBRARY_DATA_FILE", data_file)
    monkeypatch.setattr(collectors, "MUSIC_LIBRARY_DATA_FILE", data_file)
    monkeypatch.setattr(service_module, "ERROR_LOG_FILE", errors_log)

    # journalctl, as milo-backend's own INFO lines read in short-iso.
    async def _units(glob, extra=()):
        return ["milo-backend.service"]

    async def _journal_tail(*, units=(), kernel=False, **_):
        if kernel:
            return []
        return [
            "2026-09-04T16:56:18+02:00 milo python[812]: INFO backend.core.network - "
            f"Connecting to WiFi network: {EXCLUDED_SENTINELS['wifi ssid']} "
            "(password provided: True)",
            "2026-09-04T16:56:19+02:00 milo python[812]: INFO backend.core.network - "
            f"Forgot WiFi network: {EXCLUDED_SENTINELS['forgotten wifi ssid']}",
            # Lines in no shape the Redactor reads: only what the unit knows
            # (BlueZ's devices, the audio state, the settings) takes them out.
            "2026-09-04T16:56:20+02:00 milo python[812]: INFO:source.bluetooth.avrcp:"
            f"player of {EXCLUDED_SENTINELS['bluetooth device name']} gone",
            "2026-09-04T16:56:20+02:00 milo shairport-sync[700]: connection from "
            f"{EXCLUDED_SENTINELS['airplay sender']}: playing "
            f"{EXCLUDED_SENTINELS['title playing']} by {EXCLUDED_SENTINELS['artist playing']}",
            "2026-09-04T16:56:21+02:00 milo python[812]: INFO:backend.hardware.bt_remote:"
            f"remote filter {EXCLUDED_SENTINELS['bt remote filter']} matched nothing",
            # Lines in a shape: what nothing knows any more is taken out by it.
            "2026-09-04T16:56:22+02:00 milo bluetoothd[402]: a2dp-sink profile connect "
            f"failed for {EXCLUDED_SENTINELS['bluetooth address']}: Protocol not available",
            "2026-09-04T16:56:23+02:00 milo go-librespot[920]: level=info "
            f'msg="accepted zeroconf from {EXCLUDED_SENTINELS["spotify sender"]}" '
            f'username="{EXCLUDED_SENTINELS["spotify account"]}"',
            # The Connect Spotify refused, which is when a report gets generated.
            "2026-09-04T16:56:23+02:00 milo go-librespot[920]: level=error "
            f'msg="failed creating new session from {EXCLUDED_SENTINELS["spotify sender"]}" '
            'error="failed authenticating with login5: TRY_AGAIN_LATER"',
            "2026-09-04T16:56:24+02:00 milo go-librespot[920]: level=info "
            f'msg="loaded track \\"{EXCLUDED_SENTINELS["title played earlier"]}\\" '
            '(paused: false, position: 0ms, duration: 215000ms, prefetched: false)"',
        ]

    async def _bluetooth_devices(timeout):
        return {EXCLUDED_SENTINELS["bluetooth address"]: EXCLUDED_SENTINELS["bluetooth device name"]}

    monkeypatch.setattr(probes, "list_milo_units", _units)
    monkeypatch.setattr(probes, "journal_tail", _journal_tail)
    monkeypatch.setattr(probes, "bluetooth_devices", _bluetooth_devices)

    # A satellite that answers, with its own journal in the text it sends back.
    async def _satellites(clients, labels):
        return [{
            "reachable": True,
            "hostname": "milo-client",
            "text": (
                f"{SATELLITE_ALLOWED_LINE}\n"
                "2026-09-04T16:56:20+00:00 milo-client python[611]: INFO app - "
                f"WiFi '{EXCLUDED_SENTINELS['wifi ssid']}' saved as a standby link"
            ),
            "unavailable": [],
            "label": labels[client.mac_id],
            "mac_id": client.mac_id,
            "ip": client.ip,
            "registry_online": True,
        }]

    monkeypatch.setattr(satellite, "collect_all", _satellites)

    zone = Zone(
        name=EXCLUDED_SENTINELS["zone name"],
        id=EXCLUDED_SENTINELS["zone id"],
        client_ids=[ALLOWED_SENTINELS["client mac"]],
    )
    client = Client(
        mac_id=ALLOWED_SENTINELS["client mac"],
        name=EXCLUDED_SENTINELS["client name"],
        ip=ALLOWED_SENTINELS["client ip"],
        zone_id=EXCLUDED_SENTINELS["zone id"],
    )

    class _Registry:
        def get_all_clients(self):
            return {client.mac_id: client}

        def get_all_zones(self):
            return {zone.id: zone}

        def get_client_equalizer(self, mac):
            return EqualizerSettings()

    class _Settings:
        async def get_all_settings(self):
            return {
                "language": "french",
                "hardware": {
                    "bt_remote": {
                        "enabled": True,
                        "device_name_filter": EXCLUDED_SENTINELS["bt remote filter"],
                        "key_map": {},
                    }
                },
            }

    class _Network:
        """NetworkManager as NetworkService reads it: the one network it holds."""

        async def known_ssids(self):
            return {EXCLUDED_SENTINELS["wifi ssid"]}

    class _StateMachine:
        """An AirPlay session whose sender a failed restart named in its error."""

        def get_current_state(self):
            return {
                "source": "airplay",
                "switching": False,
                "service": "failed",
                "service_error": {
                    "reason": "start_failed",
                    "message": f"shairport-sync dropped {EXCLUDED_SENTINELS['airplay sender']}",
                },
                "availability": {},
                "session": {
                    "id": "c0ffee",
                    "phase": "playing",
                    "title": EXCLUDED_SENTINELS["title playing"],
                    "artist": EXCLUDED_SENTINELS["artist playing"],
                    "album": None,
                    "artwork": None,
                    "senders": [EXCLUDED_SENTINELS["airplay sender"]],
                },
                "details": None,
            }

    return {
        "registry_service": _Registry(),
        "settings_service": _Settings(),
        "network_service": _Network(),
        "state_machine": _StateMachine(),
    }


@pytest.mark.asyncio
async def test_no_excluded_value_survives_a_real_report(planted):
    """The fixture's whole excluded half must be absent from the file.

    Including from the log lines, where nothing is projected — that half is what
    the Redactor is for, and it is the half that a change to the exporter can
    quietly stop doing.
    """
    from backend.core.system.diagnostic import DiagnosticService

    result = await DiagnosticService(**planted).generate()

    for name, sentinel in EXCLUDED_SENTINELS.items():
        assert sentinel not in result["report"], f"{name} leaked into the report"
    assert "deadbeefsentineltoken" not in result["report"]


@pytest.mark.asyncio
async def test_the_allowed_values_do_survive_a_real_report(planted):
    """The counter-proof, and the reason the test above can be trusted.

    A report that collected nothing satisfies every "must not contain" rule
    ever written. These three are the ones the maintainer cannot diagnose a
    multiroom or a mount fault without, so they have to be there.
    """
    from backend.core.system.diagnostic import DiagnosticService

    result = await DiagnosticService(**planted).generate()

    for name, sentinel in ALLOWED_SENTINELS.items():
        assert sentinel in result["report"], f"{name} is missing from the report"
    # And the positional labels that replaced the excluded names are there, so
    # the report is still cross-readable.
    assert "client-1" in result["report"]
    assert "zone-1" in result["report"]
    assert "share-1" in result["report"]
    # The network NetworkManager holds is named by its label, and the satellite
    # block is still there, sanitized rather than dropped.
    assert "wifi-1" in result["report"]
    assert "===== SATELLITES =====" in result["report"]
    assert SATELLITE_ALLOWED_LINE in result["report"]
    # The phone, the sender and the tracks are named by their labels: the
    # lines that carried them are still in the file, and still say what
    # happened.
    for line in (
        "player of bt-1 gone",
        "a2dp-sink profile connect failed for bt-1: Protocol not available",
        "connection from sender-1: playing title-1 by artist-1",
        "start_failed — shairport-sync dropped sender-1",
        'accepted zeroconf from sender-2" username="account-1"',
        'failed creating new session from sender-2" error=',
        "ERROR source.spotify - failed creating new session from sender-2: failed authenticating",
        'loaded track \\"title-2\\"',
        "remote filter bt-remote-filter matched nothing",
    ):
        assert line in result["report"], line



# --------------------------------------------------------------------------- #
# The logs page's system services: the WiFi and Bluetooth daemons' own lines
# --------------------------------------------------------------------------- #

def test_the_wifi_daemons_are_read_by_the_page_only_and_never_by_the_report():
    """The report pastes whole journals into a public issue and keeps both
    WiFi daemons out; the page reads them, at warning only, through the
    Redactor told the unit. A WiFi unit admitted to the report, or one the
    page reads without the hint, carries the SSIDs both exist to remove."""
    assert wl.WIFI_UNITS <= set(wl.JOURNAL_EXCLUDED_UNITS)
    assert wl.WIFI_UNITS <= set(wl.LOGS_PAGE_SYSTEM_UNITS)
    for unit, reason in wl.LOGS_PAGE_SYSTEM_UNITS.items():
        assert len(reason) > 20, f"{unit} is read without a usable reason"


@pytest.mark.parametrize("line, kept", [
    # wpa_supplicant, printf-escaped, on any line whatever wrote it.
    ('wlan0: CTRL-EVENT-SSID-TEMP-DISABLED id=0 ssid="Sentinel \\"Net\\"" auth_failures=1',
     'ssid="<wifi>" auth_failures=1'),
    ("wlan0: CTRL-EVENT-SSID-REENABLED id=0 ssid=\"Sentinel Net\"", 'ssid="<wifi>"'),
    ("wlan0: Trying to associate with SSID 'Sentinel Net'", "SSID '<wifi>'"),
    # NetworkManager names a profile after its SSID.
    ("device (wlan0): Activation: starting connection 'Sentinel Net' (4c1e…)",
     "starting connection '<wifi>' (4c1e…)"),
])
def test_a_network_a_wifi_daemon_names_is_redacted_without_knowing_it(line, kept):
    """A network the unit never saved — a neighbor's, one forgotten since — is
    in no list the Redactor is built from: only its shape takes it out."""
    out = wl.Redactor()(line)
    assert "Sentinel" not in out and kept in out, out


def test_a_wifi_units_line_is_about_a_network_whatever_it_says():
    """NetworkManager and wpa_supplicant write shapes nobody listed: on their
    lines every quoted value is taken as a network, all but a path, and an
    access point's address (which a public database locates) is labeled."""
    redactor = wl.Redactor()
    joined = redactor(
        'device (wlan0): Activation: (wifi) Stage 2 of 5 successful. Connected to wireless network "Sentinel"',
        unit="NetworkManager.service",
    )
    assert joined.endswith('Connected to wireless network "<wifi>"'), joined
    script = redactor(
        "dispatcher: (17) Script '/etc/NetworkManager/dispatcher.d/01-ifupdown' exited with status 1.",
        unit="NetworkManager.service",
    )
    assert "'/etc/NetworkManager/dispatcher.d/01-ifupdown'" in script
    lost = redactor("wlan0: CTRL-EVENT-BEACON-LOSS bssid=7c:4c:a5:1e:22:10", unit="wpa_supplicant.service")
    again = redactor("wlan0: Associated with 7C:4C:A5:1E:22:10", unit="wpa_supplicant.service")
    assert lost.endswith("bssid=ap-1") and again.endswith("with ap-1"), (lost, again)
    # The same address on a line of a unit that is not a WiFi daemon is a
    # multiroom client's, which the logs keep.
    assert "7c:4c:a5:1e:22:10" in redactor("client 7c:4c:a5:1e:22:10 joined", unit="milo-backend.service")


def test_a_bluetooth_units_line_is_about_bluetooth_whatever_it_says():
    """bluetoothd names a phone by its address on lines that say neither
    Bluetooth nor A2DP: the unit says it, and the address is the phone's."""
    line = "profiles/audio/avdtp.c:avdtp_connect_cb() connect to 4C:2E:5E:11:22:33: Connection refused (111)"
    out = wl.Redactor()(line, unit="bluetooth.service")
    assert "4C:2E:5E:11:22:33" not in out and "connect to bt-1:" in out, out


@pytest.mark.parametrize("line", [
    # netplan's keyfile: the SSID URI-escaped, which no known-network
    # pattern matches.
    '<warn>  [1764230400.1234] keyfile: load: "/run/NetworkManager/system-connections/'
    'netplan-NM-3e5b1c2a-Maison%20Leo.nmconnection": failed to load connection: invalid connection',
    # A keyfile of the wrong mode, named after a network the unit never saved.
    '<warn>  [1764230400.1234] keyfile: load: "/etc/NetworkManager/system-connections/'
    'Voisin Box.nmconnection": failed to load connection: File permissions (644) are insecure',
])
@pytest.mark.parametrize("unit", ["NetworkManager.service", None])
def test_a_profiles_file_name_is_a_network_name(line, unit):
    """A NetworkManager profile's file is named after its network, and a
    WiFi daemon's line keeps an absolute path whole: the file's name is taken
    out first, on any line, by a Redactor that knows the network under another
    spelling or not at all."""
    redactor = wl.Redactor.build(
        client_labels={}, client_names={}, zone_labels={}, zone_names={},
        share_labels={}, share_names={}, usb_strings={}, ssid_labels={"Maison Leo": "wifi-1"},
    )
    out = redactor(line, unit=unit)
    for name in ("Maison", "Voisin", "Leo"):
        assert name not in out, out
    assert "system-connections/" in out and "<wifi>.nmconnection" in out, out
