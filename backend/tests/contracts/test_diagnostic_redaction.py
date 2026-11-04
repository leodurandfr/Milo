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
import functools
import json
import re
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.settings import create_settings_router
from backend.core.multiroom.models import Client, EqualizerSettings, Zone
from backend.core.settings import SettingsService
from backend.core.system.diagnostic import whitelist as wl
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
    """A declared `x.*` covers `x` itself: the map is one field, its keys data."""
    return path in allowed or f"{path}.*" in allowed


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
    assert counts["hardware.json"] == 14, counts
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
    address it registered on every start. Link-local and ULA are local, and are
    what a routing or an mDNS problem is actually read from.
    """
    out = wl.strip_global_ipv6(
        "Registering 2a01:e0a:1048:b5b0:e079:41ff:e835:8628 on eth0; "
        "nameserver fd7a:115c:a1e0::53; peer fe80::1; host 192.168.1.55"
    )
    assert "2a01" not in out and "<global-ipv6>" in out
    assert "fd7a:115c:a1e0::53" in out
    assert "fe80::1" in out
    assert "192.168.1.55" in out
    # A timestamp is not an address.
    assert wl.strip_global_ipv6("2026-09-04T18:05:20+02:00 ok") == "2026-09-04T18:05:20+02:00 ok"


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


# A profile name is `milo-<ssid>` (or netplan's, or the SSID itself), and an
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
        ]

    monkeypatch.setattr(probes, "list_milo_units", _units)
    monkeypatch.setattr(probes, "journal_tail", _journal_tail)

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

    return {
        "registry_service": _Registry(),
        "settings_service": _Settings(),
        "network_service": _Network(),
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

