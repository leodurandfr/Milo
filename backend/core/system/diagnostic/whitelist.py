# backend/core/system/diagnostic/whitelist.py
"""What a diagnostic report is allowed to say — the single declaration.

The report leaves the appliance in the hands of a stranger who pastes it into a
public issue, so its content is decided here by ENUMERATION, never by removal: a
structured section is built by projecting one of the `*_ALLOWED` sets onto its
source dict, so a field nobody declared cannot reach the file even by accident.
Every field the upstream models declare must appear in exactly one of the two
halves — the allowed set, or `*_EXCLUDED` with the reason it stays home — and
`backend/tests/contracts/test_diagnostic_redaction.py` goes red the day a new
one appears upstream and nobody decided.

Two shapes of secret, both measured in this tree rather than imagined:

  * Real secrets. The Qobuz token (`/var/lib/milo/qobuz/credentials.json`),
    a share password (a root-only cred file written by milo-mount — never in
    music_library_data.json), the WiFi PSK and SSID (NetworkManager). None of
    them is read by any collector: no path here points at them.
  * Things the user typed or named. A share id embeds the display name the user
    chose (`nas-leo-d7992dfe`), a client is called "Canapé", a USB key "iPod de
    Léo", a Bluetooth remote filter is whatever was typed. None is a secret and
    all of them identify a household, so they are replaced by positional labels
    that carry the same diagnostic value: `client-1` beside its MAC says as much
    as "Canapé" and says nothing about who lives there.

The two free-text sections (errors.log, the journal) cannot be projected field
by field. Their whitelist is the set of admitted UNITS — `milo-*` plus the two
named below, never NetworkManager or wpa_supplicant, which carry the SSID — and
one structural rule on the text itself, `strip_query_strings`.
"""
import re
from typing import Any, Dict, Iterable, Mapping, Optional


# =============================================================================
# settings.json
# =============================================================================
# Paths are dotted, `*` standing for a key whose NAME is data (a MAC, a program
# key, an IR scancode). The `multiroom.*` subtree is not listed here: the
# registry owns those records and they are declared below, per model.

SETTINGS_ALLOWED = frozenset({
    "setup_completed",
    "language",
    "volume.limit_min_db",
    "volume.limit_max_db",
    "volume.restore_last_volume",
    "volume.startup_volume_db",
    "volume.step_mobile_db",
    "volume.step_rotary_db",
    "volume.step_bt_remote_db",
    "volume.step_ir_remote_db",
    "screen.timeout_seconds",
    "screen.brightness_on",
    "screen.screensaver_enabled",
    "screen.screensaver_delay_seconds",
    "screen.ui_scale",
    "screen.color_filter_enabled",
    "screen.color_filter_warmth",
    "audio.auto_stop_delay",
    "routing.multiroom_enabled",
    "routing.equalizer_effects_enabled",
    "dock.enabled_apps",
    "radio.shazam_enabled",
    "music_library.separate_storages",
    "qobuz.allow_app_volume",
    "spotify.crossfade_duration",
    # The regulatory domain, not the network: a country code is what explains an
    # access point that will not come up and a 5 GHz band that is missing.
    "wifi.country",
    "updates.forced_versions.*",
    "mac.target_latency_ms",
    "mac.latency_profile",
    "mac.frame_length_ms",
    "fan.enabled",
    "fan.mode",
    "fan.manual_percent",
    "fan.target_temp_c",
    "fan.curve",
    "hardware.bt_remote.enabled",
    "hardware.bt_remote.key_map",
    "hardware.ir_remote.enabled",
    "hardware.ir_remote.device_id",
    "hardware.ir_remote.paired_at",
})

SETTINGS_EXCLUDED = {
    "hardware.bt_remote.device_name_filter":
        "typed by the user and usually the remote's brand or a household name; "
        "the report says configured / not configured instead",
}


# =============================================================================
# hardware.json
# =============================================================================

HARDWARE_ALLOWED = frozenset({
    "audio.id",
    "audio.volume_control",
    "screen.type",
    "screen.resolution",
    "rotary_encoder.enabled",
    "rotary_encoder.clk_pin",
    "rotary_encoder.dt_pin",
    "rotary_encoder.sw_pin",
    "ir_remote.enabled",
    "ir_remote.gpio_pin",
})

HARDWARE_EXCLUDED = {}


# =============================================================================
# Multiroom registry — Client
# =============================================================================

CLIENT_ALLOWED = frozenset({
    # The MAC and the IP are what make a multiroom fault readable at all, and
    # both are local-network identifiers the owner already shares by using the
    # network. Kept deliberately.
    "mac_id",
    "ip",
    "host",
    "zone_id",
    "speaker_type",
    "volume_control",
    "eq_independent",
    "delay_ms",
    "online",
    "is_local",
})

CLIENT_EXCLUDED = {
    "name": "the room name the user typed (\"Canapé\"); replaced by a positional "
            "label assigned in MAC order, which cross-references the same client "
            "in every section",
}


# =============================================================================
# Multiroom registry — Zone
# =============================================================================

ZONE_ALLOWED = frozenset({
    "client_ids",
    "crossover_frequency",
})

ZONE_EXCLUDED = {
    "name": "the zone name the user typed (\"Salon\"); replaced by a positional label",
    "id": "a UUID that identifies nothing outside this unit and carries no "
          "diagnostic value; the positional label replaces it, including in the "
          "clients' zone_id",
}


# =============================================================================
# Per-client equalizer record
# =============================================================================

EQUALIZER_ALLOWED = frozenset({
    "enabled",
    "filters",
    "compressor",
    "loudness",
    "active_preset",
    "mono",
    "custom_gains",
})

EQUALIZER_EXCLUDED = {}


# =============================================================================
# Music library — network shares and USB keys
# =============================================================================

SHARE_ALLOWED = frozenset({
    "type",
    # A NAS address on the local network. Without it a mount failure has no
    # subject at all.
    "host",
    "has_credentials",
    "created_at",
})

SHARE_EXCLUDED = {
    "id": "generated from the display name the user chose, so it carries it "
          "verbatim (\"nas-leo-d7992dfe\"); shares are reported by position",
    "name": "the display name the user chose",
    "path": "a share path on the user's own server, which names their folders",
    "username": "the account used to mount the share — an identifier, and half "
                "of the credential whose other half milo-mount holds",
    "domain": "the other half of that login",
}

USB_ALLOWED = frozenset({
    "last_seen",
})

USB_EXCLUDED = {
    "name": "the name the user gave the key (\"iPod de Léo\")",
    "label": "the filesystem label, which is where that name usually comes from",
    "mountpoint": "derived from the label, so it carries it, and it names a path "
                  "into the user's own music",
}


# =============================================================================
# The journal — a whitelist of units, not of fields
# =============================================================================

# Every unit this repository installs answers `milo-*`, so the glob follows a
# unit added later without anyone remembering to come back here.
JOURNAL_UNIT_GLOB = "milo-*"

# Two units outside that glob, admitted by name for what they alone answer.
JOURNAL_EXTRA_UNITS = (
    # Which name this unit advertises, and against whom it collided.
    "avahi-daemon.service",
    # A2DP link-level failures live here and nowhere else.
    "bluetooth.service",
)

JOURNAL_EXCLUDED_UNITS = {
    "NetworkManager.service": "logs the SSID of every network it touches",
    "wpa_supplicant.service": "logs the SSID, and the association exchange with it",
    "ssh.service": "logs where people connected from and nothing about Milō",
}

# The kernel ring is admitted on its own: undervoltage, ALSA xruns, CIFS
# timeouts and the OOM killer are visible there and in no unit's journal.
JOURNAL_INCLUDE_KERNEL = True


# =============================================================================
# Listening ports — asked about by number, never enumerated from the host
# =============================================================================
# `ss -ltn` in full also reports the Tailscale address and every unrelated
# listener on the box. The report asks whether each of Milō's own ports answers.

LISTEN_PORTS = {
    80: "nginx (the UI)",
    8000: "milo-backend",
    8001: "milo-client (a satellite's API; the server does not listen here)",
    1704: "snapserver stream",
    1705: "snapserver control",
    1780: "snapserver JSON-RPC",
    3678: "go-librespot API",
    4533: "navidrome",
}


# =============================================================================
# The one structural rule on free text
# =============================================================================

_QUERY = re.compile(r"(\?)[^\s\"'<>]*")

# 2000::/3 — the globally routable half of IPv6, written out or compressed.
# Link-local (fe80::) and ULA (fc00::/7) start with f and are deliberately left
# alone: they are local addresses, and the report keeps those.
_GLOBAL_IPV6 = re.compile(
    r"(?<![\w:])[23][0-9a-f]{3}:(?:[0-9a-f]{0,4}:){1,7}[0-9a-f]{0,4}(?![\w:])",
    re.IGNORECASE,
)


def strip_global_ipv6(text: str) -> str:
    """Replace globally routable IPv6 addresses; keep the local ones.

    Local IP and MAC addresses stay in this report on purpose — a multiroom
    fault cannot be read without them. A *global* IPv6 address is a different
    thing: its /64 is handed out by the ISP and identifies the subscriber line,
    not a device on the LAN. Measured in this unit's own avahi journal, which
    announces the address it registered on every start:

        avahi-daemon[…]: Registering new address record for
        2a01:e0a:1048:b5b0:e079:41ff:e835:8628 on eth0.*.

    Nothing is diagnosed from it that `eth0 is up` does not already say.
    """
    return _GLOBAL_IPV6.sub("<global-ipv6>", text)


def strip_query_strings(text: str) -> str:
    """Truncate every URL query at its `?`.

    Not a search for secrets — a rule on a shape. It exists because a query
    string is where a credential ends up when it ends up in a log line, and this
    tree has one: the Music Library streams from Navidrome over Subsonic token
    auth, so its mpv URL carries `u=<user>&t=<md5(password+salt)>&s=<salt>` —
    the token and the salt that cracks it, on one line. The producer no longer
    logs it (`shared/mpv.py`), but a journal read six hours back still holds the
    lines written before that, and errors.log holds three rotations of them.
    """
    return _QUERY.sub(r"\1…", text)


# =============================================================================
# Projection
# =============================================================================

def project(source: Mapping[str, Any], allowed: Iterable[str]) -> Dict[str, Any]:
    """The declared keys of `source`, and nothing else.

    Absent keys are absent from the result rather than None: a report that
    invents a field it did not read is worse than one that omits it. Sorted,
    because the sets above are frozensets and their iteration order changes
    between processes — two reports from one unit must be diffable.
    """
    return {key: source[key] for key in sorted(allowed) if key in source}


class Redactor:
    """Enforces the exclusions above in text that cannot be projected.

    A structured section is built by projecting a whitelist, so an excluded
    field simply never reaches it. Free text has no such structure — and the
    excluded values turn up in it anyway. Measured on this unit, in errors.log:

        WARNING source.music_library.shares - Share nas-leo-d7992dfe stopped
        answering (3 consecutive probes); hiding it until it comes back

    The share id is excluded because it is generated from the display name the
    user typed, and here it is in a log line. Not a reason to change the
    producer: on the unit itself that id is exactly the handle an operator
    needs. It is a reason for the exclusion to follow the value wherever it
    goes, which is what this does — every excluded value the appliance knows
    about is replaced by the same positional label the structured sections use,
    so a report stays cross-readable while carrying none of them.

    Longest first, so `nas-leo-d7992dfe` is replaced before a share named
    `nas-leo` could eat half of it. Values under three characters are skipped:
    a room called "Ho" would rewrite the middle of unrelated words, and
    over-redaction that destroys the log helps nobody either.
    """

    MIN_LENGTH = 3

    def __init__(self, replacements: Optional[Dict[str, str]] = None):
        self._patterns = []
        for value, label in sorted(
            (replacements or {}).items(), key=lambda kv: -len(kv[0])
        ):
            if not value or len(value) < self.MIN_LENGTH:
                continue
            self._patterns.append(
                (re.compile(rf"(?<!\w){re.escape(value)}(?!\w)"), label)
            )

    def __call__(self, text: str) -> str:
        text = strip_global_ipv6(strip_query_strings(text))
        for pattern, label in self._patterns:
            text = pattern.sub(label, text)
        return text

    @classmethod
    def build(
        cls,
        *,
        client_labels: Dict[str, str],
        client_names: Dict[str, str],
        zone_labels: Dict[str, str],
        zone_names: Dict[str, str],
        share_labels: Dict[str, str],
        share_names: Dict[str, str],
        usb_strings: Dict[str, str],
    ) -> "Redactor":
        """One map from every excluded value this unit holds to its label."""
        replacements: Dict[str, str] = {}
        for mac, name in client_names.items():
            if name:
                replacements[name] = client_labels.get(mac, "client-?")
        for zone_id, name in zone_names.items():
            label = zone_labels.get(zone_id, "zone-?")
            replacements[zone_id] = label
            if name:
                replacements[name] = label
        for share_id, label in share_labels.items():
            replacements[share_id] = label
            name = share_names.get(share_id)
            if name:
                replacements[name] = label
        replacements.update(usb_strings)
        return cls(replacements)
