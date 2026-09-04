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
import json
from pathlib import Path

import pytest

from backend.core.multiroom.models import Client, EqualizerSettings, Zone
from backend.core.settings import SettingsService
from backend.core.system.diagnostic import whitelist as wl

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


def _hardware_paths() -> frozenset:
    """hardware.json's normalised shape, from the accessor that defines it."""
    from backend.hardware.registry import AUDIO_CARDS  # noqa: F401 -- import guard

    paths = set()
    for section, keys in {
        "audio": ("id", "volume_control"),
        "screen": ("type", "resolution"),
        "rotary_encoder": ("enabled", "clk_pin", "dt_pin", "sw_pin"),
        "ir_remote": ("enabled", "gpio_pin"),
    }.items():
        paths.update(f"{section}.{key}" for key in keys)
    return frozenset(paths)


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
    assert counts["hardware.json"] == 10, counts
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
    what a Tailscale or an mDNS problem is actually read from.
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
    )
    out = redactor(
        "Share nas-leo-d7992dfe stopped answering; zone Salon (zone-uuid) holds "
        "Canapé; mounted /media/milo/nas-leo-d7992dfe and iPod de Léo"
    )
    for secret in ("nas-leo-d7992dfe", "Salon", "zone-uuid", "Canapé", "iPod de Léo"):
        assert secret not in out, secret
    assert out.count("share-1") == 2
    assert "client-1" in out and "zone-1" in out and "usb-1" in out


def test_a_short_value_is_not_substituted_into_unrelated_words():
    """Over-redaction that destroys the log helps nobody either.

    A room named "Ho" would rewrite the middle of every word containing it, so
    values under three characters are skipped and the rest match on a word
    boundary — `Salonique` is not the zone `Salon`.
    """
    redactor = wl.Redactor({"Ho": "client-1", "Salon": "zone-1"})
    assert redactor("Ho and Salonique and Salon") == "Ho and Salonique and zone-1"


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
}

ALLOWED_SENTINELS = {
    "client mac": "de:ad:be:ef:00:01",
    "client ip": "192.168.55.66",
    "share host": "192.168.55.77",
}


@pytest.fixture
def planted(monkeypatch, tmp_path):
    """A unit whose every excluded field carries a unique, greppable value."""
    from backend.core.system.diagnostic import collectors
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
    )

    monkeypatch.setattr(service_module, "MUSIC_LIBRARY_DATA_FILE", data_file)
    monkeypatch.setattr(collectors, "MUSIC_LIBRARY_DATA_FILE", data_file)
    monkeypatch.setattr(service_module, "ERROR_LOG_FILE", errors_log)

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

    return _Registry(), _Settings()


@pytest.mark.asyncio
async def test_no_excluded_value_survives_a_real_report(planted, monkeypatch):
    """The fixture's whole excluded half must be absent from the file.

    Including from the log lines, where nothing is projected — that half is what
    the Redactor is for, and it is the half that a change to the exporter can
    quietly stop doing.
    """
    from backend.core.system.diagnostic import DiagnosticService

    registry, settings = planted
    # No satellite probe: the fixture's client is remote, and a real HTTP fan-out
    # here would make the test about the network.
    monkeypatch.setattr(
        "backend.core.system.diagnostic.satellite.collect_all",
        lambda clients, labels: _empty(),
    )
    result = await DiagnosticService(
        registry_service=registry, settings_service=settings
    ).generate()

    for name, sentinel in EXCLUDED_SENTINELS.items():
        assert sentinel not in result["report"], f"{name} leaked into the report"
    assert "deadbeefsentineltoken" not in result["report"]


@pytest.mark.asyncio
async def test_the_allowed_values_do_survive_a_real_report(planted, monkeypatch):
    """The counter-proof, and the reason the test above can be trusted.

    A report that collected nothing satisfies every "must not contain" rule
    ever written. These three are the ones the maintainer cannot diagnose a
    multiroom or a mount fault without, so they have to be there.
    """
    from backend.core.system.diagnostic import DiagnosticService

    registry, settings = planted
    monkeypatch.setattr(
        "backend.core.system.diagnostic.satellite.collect_all",
        lambda clients, labels: _empty(),
    )
    result = await DiagnosticService(
        registry_service=registry, settings_service=settings
    ).generate()

    for name, sentinel in ALLOWED_SENTINELS.items():
        assert sentinel in result["report"], f"{name} is missing from the report"
    # And the positional labels that replaced the excluded names are there, so
    # the report is still cross-readable.
    assert "client-1" in result["report"]
    assert "zone-1" in result["report"]
    assert "share-1" in result["report"]


async def _empty():
    return []
