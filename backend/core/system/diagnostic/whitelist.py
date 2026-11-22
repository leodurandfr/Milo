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
    music_library_data.json), the WiFi PSK (NetworkManager). None of them is
    read by any collector: no path here points at them.
  * Things the user typed or named. A share id embeds the display name the user
    chose (`nas-leo-d7992dfe`), a client is called "Canapé", a USB key "iPod de
    Léo", a Bluetooth remote filter is whatever was typed. None is a secret and
    all of them identify a household, so they are replaced by positional labels
    that carry the same diagnostic value: `client-1` beside its MAC says as much
    as "Canapé" and says nothing about who lives there.

  * What the household plays, and from what. A phone paired over Bluetooth is
    called "iPhone de Léo", and so is the AirPlay sender and the device that
    hands Spotify a session; a Bluetooth address names that phone wherever it
    goes; a title, an artist or an album says what someone listens to. None of
    it is printed by a structured section, and all of it is in the logs. Each
    is replaced by a positional label (`bt-1`, `sender-1`, `title-1`,
    `artist-1`…), the same value by the same label everywhere in the file —
    where the unit knows the value, or a line writes it in a shape read here.
    What a playback fault is read from is kept on purpose, and
    `STATE_NOT_REDACTED` says why for each: a radio station's name, a Spotify
    URI, a CD's disc id. And the journals of shairport-sync, the Tidal daemon,
    the Qobuz sidecar and Navidrome were never measured naming a track: only
    the values the state holds, and the logfmt fields read below, are taken
    out of them.

The free-text sections (errors.log, the journal, each satellite's block) cannot
be projected field by field. Their whitelist is the set of admitted UNITS —
`milo-*` plus the two named below, never NetworkManager or wpa_supplicant — and
structural rules on the text itself. Keeping those two units out does not keep
the SSID out: milo-backend and milo-client log the networks they save, join,
forget and hand to a satellite themselves. So the networks NetworkManager holds
are replaced by `wifi-N` like any other excluded value, and `strip_wifi_names`
removes a network named in one of the shapes Milō's own lines use, which is what
still catches one forgotten since. The devices and the listening follow the same
two halves: what the unit knows now (the devices BlueZ holds, the audio state)
is labeled wherever it appears, and `LISTENING_SHAPES` takes the value out of
the lines that name one, which is what catches the track played three hours ago.
"""
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple


# =============================================================================
# settings.json
# =============================================================================
# Paths are dotted, `*` standing for a key whose NAME is data (a MAC, a program
# key, an IR scancode). The `multiroom.*` subtree is not listed here: the
# registry owns those records and they are declared below, per model.

SETTINGS_ALLOWED = frozenset({
    "setup_completed",
    "setup_awaiting_network",
    "language",
    "volume.limit_min_db",
    "volume.limit_max_db",
    "volume.restore_last_volume",
    "volume.startup_volume_db",
    "volume.step_interface_db",
    "volume.step_rotary_db",
    "volume.step_bt_remote_db",
    "volume.step_ir_remote_db",
    "screen.timeout_seconds",
    "screen.brightness_on",
    "screen.auto_player_enabled",
    "screen.auto_player_delay_seconds",
    "screen.ui_scale",
    "screen.color_filter_enabled",
    "screen.color_filter_warmth",
    "screen.edge_inset_mm",
    "screen.theme",
    "audio.auto_stop_delay",
    "audio.resume_window",
    "audio.normalization.radio",
    "audio.normalization.podcast",
    "audio.normalization.cd",
    "audio.normalization.music_library",
    "routing.multiroom_enabled",
    "routing.equalizer_effects_enabled",
    "dock.enabled_apps",
    "dock.volume.milo_screen",
    "dock.volume.desktop_browser",
    "dock.volume.phone_browser",
    "dock.volume.ios_app",
    "radio.shazam_enabled",
    "music_library.separate_storages",
    "qobuz.allow_app_volume",
    "spotify.crossfade_duration",
    "spotify.allow_app_volume",
    # The regulatory domain, not the network: a country code is what explains an
    # access point that will not come up and a 5 GHz band that is missing.
    "wifi.country",
    "updates.forced_versions.*",
    # Per MAC, the app push a satellite did not take: payload, verdict, time and
    # the satellite's own error line — a line its journal block already carries.
    "updates.satellite_app_failures.*",
    "mac.target_latency_ms",
    "mac.latency_profile",
    "mac.frame_length_ms",
    "mac.packet_length_ms",
    "mac.fec_block_source",
    "mac.fec_block_repair",
    "mac.packet_interleaving",
    "fan.enabled",
    "fan.mode",
    "fan.manual_percent",
    "fan.target_temp_c",
    "hardware.bt_remote.enabled",
    "hardware.bt_remote.key_map.*",
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
    "audio.overlay",
    "audio.volume_control",
    "screen.type",
    "screen.resolution",
    "rotary_encoder.enabled",
    "rotary_encoder.clk_pin",
    "rotary_encoder.dt_pin",
    "rotary_encoder.sw_pin",
    "ir_remote.enabled",
    "ir_remote.gpio_pin",
    "power_button.enabled",
    "power_button.led_gpio_pin",
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
    "gain_db",
    "online",
    "is_local",
    "audio_card_missing",
    "other_server",
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
# The audio state — what plays, and who sends it
# =============================================================================
# The report prints none of the state's content: the sources section shows the
# phase and the availability, never the session. These two halves decide which
# of the state's text the Redactor is taught, so that a value is labeled
# wherever free text carries it — go-librespot names the track it loads in its
# journal, the Bluetooth source names the phone that connected. A path is the
# wire's own: `[]` for a list item, and a `details` block's `kind` as its own
# segment. The value is the label's kind. A block the wire passes on as its
# producer built it (`Dict[str, Any]`: a Subsonic track, a podcast episode) is
# decided twice: its `.*` in STATE_NOT_REDACTED for every key no type declares,
# and the keys that name what plays here, by the spelling the producer uses.
# One value can sit on a path of each half — radio writes its station's name as
# the session's title and album — and then the keep wins (`state_values`).

STATE_REDACTED = {
    "session.title": "title",
    "session.artist": "artist",
    "session.album": "album",
    # The Bluetooth phone, the AirPlay sender, the Macs streaming over ROC.
    "session.senders[]": "sender",
    "resume.title": "title",
    "resume.artist": "artist",
    "resume.album": "album",
    "details.radio.track.title": "title",
    "details.radio.track.artist": "artist",
    "details.cd.disc.album": "album",
    "details.cd.disc.artist": "artist",
    "details.cd.disc.tracks[].title": "title",
    # The queue window, as Navidrome's Subsonic API returns its tracks: the
    # ones played before the current one are what was listened to, too.
    "details.music_library.queue[].title": "title",
    "details.music_library.queue[].artist": "artist",
    "details.music_library.queue[].album": "album",
    # The episode as sources/podcast/rss_parser.py builds it.
    "details.podcast.episode.name": "title",
    "details.podcast.episode.podcast.name": "artist",
    # The account go-librespot is signed in as is a person.
    "details.spotify.account": "account",
    # An album's title, or the name someone gave a playlist.
    "details.spotify.context_name": "title",
    "details.spotify.artists[].name": "artist",
    "details.spotify.queue[].title": "title",
    "details.spotify.queue[].artist": "artist",
    # As the device's own app names it: "iPhone de Léo".
    "details.spotify.remote.device_name": "sender",
    "details.spotify.remote.title": "title",
    "details.spotify.remote.artist": "artist",
    "details.spotify.remote.album": "album",
    "details.spotify.jam.members[].name": "account",
}

STATE_NOT_REDACTED = {
    "service_error.message":
        "Milō's own sentence about a start that failed; the sources section "
        "prints it through the Redactor like any free text",
    "session.id": "a UUID Milō generated for the session, which names nothing",
    "session.artwork": "a cover URL, which the report never prints",
    "resume.artwork": "a cover URL, which the report never prints",
    "controls[]": "the command names a source takes, a closed vocabulary",
    "details.radio.station.id": "a Radio Browser catalog id, the same for every listener",
    "details.radio.station.name":
        "a public broadcaster's name, the same for every listener, and what a "
        "radio fault is read from (the stream that will not load) — kept where "
        "radio also writes it, as the session's and the resume point's album, "
        "and as their title while no track is known",
    "details.radio.station.url": "the broadcaster's public stream URL",
    "details.radio.station.country": "the broadcaster's country, from the catalog",
    "details.radio.station.genre": "the broadcaster's genre, from the catalog",
    "details.radio.station.favicon": "the broadcaster's logo URL, from the catalog",
    "details.radio.station.codec": "the stream's codec, which is what a decode fault is read from",
    "details.radio.track.artwork": "a cover URL, which the report never prints",
    "details.music_library.queue[].*":
        "Navidrome's track as it answers it: its ids, durations, codec and "
        "path, none of which a Milō line or a daemon was seen to write. Its "
        "title, artist and album are in STATE_REDACTED",
    "details.podcast.episode.*":
        "the episode as the RSS feed describes it, public and the same for "
        "every listener: its ids, dates, URLs and description. Its name and "
        "its show's are in STATE_REDACTED",
    "details.music_library.track_id": "a Navidrome id, opaque outside this unit",
    "details.music_library.album_id": "a Navidrome id, opaque outside this unit",
    "details.music_library.artist_id": "a Navidrome id, opaque outside this unit",
    "details.music_library.context_id": "a Navidrome id, opaque outside this unit",
    "details.cd.disc.id":
        "the MusicBrainz disc id, the key a CD lookup that failed is read from",
    "details.cd.disc.year": "a release year, which names nobody",
    "details.cd.disc.cover_url": "a cover URL, which the report never prints",
    "details.spotify.context_uri":
        "a Spotify URI, what a load go-librespot refused is read from",
    "details.spotify.track_uri":
        "a Spotify URI, what a load go-librespot refused is read from",
    "details.spotify.album_uri":
        "a Spotify URI, what a load go-librespot refused is read from",
    "details.spotify.artists[].uri":
        "a Spotify URI, what a load go-librespot refused is read from",
    "details.spotify.queue[].uri":
        "a Spotify URI, what a load go-librespot refused is read from",
    "details.spotify.queue[].thumbnail": "a cover URL, which the report never prints",
    "details.spotify.remote.device_type":
        "Spotify's type for the device (smartphone, computer), which names nobody",
    "details.spotify.remote.artwork": "a cover URL, which the report never prints",
    "details.spotify.remote.track_uri":
        "a Spotify URI, what a load go-librespot refused is read from",
}


def state_values(state: Mapping[str, Any]) -> List[Tuple[str, str]]:
    """(kind, value) for every `STATE_REDACTED` text `state` holds, in wire order.

    A value the state also holds on a path `STATE_NOT_REDACTED` names is kept,
    wherever else it sits: the decision to keep it is about the value, and
    teaching it from its other path would undo that decision in every log line.
    Radio is the case it exists for: its session's title and album, and its
    resume point's, are the station's name whenever no track is known, which is
    `details.radio.station.name`. A key inside a `.*` block is not a path that
    dict names, so a block kept as a whole keeps nothing this way.
    """
    found: List[Tuple[str, str]] = []
    kept: Set[str] = set()

    def walk(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            kind = value.get("kind")
            prefix = f"{path}.{kind}" if path and isinstance(kind, str) else path
            for key, child in value.items():
                if key != "kind":
                    walk(child, f"{prefix}.{key}" if prefix else key)
        elif isinstance(value, list):
            for item in value:
                walk(item, f"{path}[]")
        elif isinstance(value, str) and value:
            if path in STATE_REDACTED:
                found.append((STATE_REDACTED[path], value))
            elif path in STATE_NOT_REDACTED:
                kept.add(value)

    walk(state, "")
    return [(kind, value) for kind, value in found if value not in kept]


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
# `ss -ltn` in full also reports any tunnel address and every unrelated
# listener on the box. The report asks whether each of Milō's own ports answers.

LISTEN_PORTS = {
    80: "nginx (the UI)",
    8000: "milo-backend",
    8001: "milo-client (a satellite's API; the server does not listen here)",
    1704: "snapserver stream",
    3678: "go-librespot API",
    4533: "navidrome",
}


# =============================================================================
# The structural rules on free text
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
    announces the address it registered on every start (shown here with a
    2001:db8::/32 stand-in for the subscriber's own):

        avahi-daemon[…]: Registering new address record for
        2001:db8:0:1:a4c3:1eff:fe2b:9d55 on eth0.*.

    Nothing is diagnosed from it that `eth0 is up` does not already say.
    """
    return _GLOBAL_IPV6.sub("<global-ipv6>", text)


# The shapes Milō's own lines give a network name, read off every logger call
# that passes one — `test_every_logged_ssid_shape_is_redacted_without_knowing_it`
# renders each of them and goes red on one these do not read. A quoted value is
# taken on a line that is about a network; an unquoted one only after its own
# lead and up to the end of the line or a known suffix, which holds because a
# line is sanitized whole, before it is capped.
_WIFI_LINE = re.compile(r"wi-?fi|ssid|home network|hotspot|adopt", re.IGNORECASE)
_QUOTED = re.compile(r"(?<!\w)'([^\n]*?)'(?!\w)")
_UNQUOTED = (
    re.compile(
        r"(\b(?:connected to WiFi|WiFi network|WiFi profile): )"
        r"(.+?)(?= \((?:password provided|ssid=)|$)",
        re.IGNORECASE,
    ),
    re.compile(r"(\bdelete WiFi profile )(.+?)(?=: |$)", re.IGNORECASE),
    re.compile(r"(\(ssid=)(.+)(?=\)$)", re.IGNORECASE),
    # An nmcli that timed out is logged with its argv, which names the profile
    # and, for an `add`, the SSID: everything after the verb goes.
    re.compile(
        r"(\bnmcli timed out: nmcli (?:.*? )?connection (?:add|delete|modify|show|up) )"
        r"(?!--active$)(.+)$"
    ),
)
# What a known network was already replaced by, which says more than `<wifi>`.
_WIFI_LABEL = re.compile(r"(?:milo-)?wifi-\d+")
# The appliance's own names — the setup hotspot every Milō raises, its units and
# hosts. Never a household's, and an SSID spelled like one would rewrite every
# unit and host name in the file.
_APPLIANCE_NAME = re.compile(r"mil[oō](?:-client)?(?:-\d+)?", re.IGNORECASE)


def _kept(name: str) -> bool:
    return bool(_WIFI_LABEL.fullmatch(name) or _APPLIANCE_NAME.fullmatch(name))


def strip_wifi_names(text: str, about_a_network: Optional[bool] = None) -> str:
    """Replace a network name written in one of Milō's own shapes by `<wifi>`.

    `about_a_network` is whether the line was about a network before any label
    was put in it: a `wifi-N` the Redactor wrote does not make every other
    quoted value on its line a network name.
    """
    if about_a_network is None:
        about_a_network = bool(_WIFI_LINE.search(text))
    if about_a_network:
        text = _QUOTED.sub(
            lambda m: m.group(0) if _kept(m.group(1)) else "'<wifi>'", text
        )
    for pattern in _UNQUOTED:
        text = pattern.sub(
            lambda m: m.group(0) if _kept(m.group(2)) else f"{m.group(1)}<wifi>", text
        )
    return text


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


# A Python repr, as `%r` and `!r` write a title: single-quoted, or double-quoted
# when the value holds a single quote ("Don't Stop").
_REPR = r"(?:'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\")"
_MAC_TEXT = r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}"
_IPV4 = r"\d{1,3}(?:\.\d{1,3}){3}"
# Where a message begins in the two line formats the backend writes — the
# journal's `INFO:logger:` and errors.log's `INFO logger - ` — or a bare line.
# A value read from there stops at a colon, so a bare `^` never takes the
# prefix with it.
_AT_MESSAGE = r"(?:^|\b(?:DEBUG|INFO|WARNING|ERROR|CRITICAL)(?::[\w.]+:| [\w.]+ - ))"
# A `key=value` field's value: logrus quotes one only when it holds a space or
# a quote, and a Python repr writes it single-quoted. An absent value (`None`,
# Go's `nil`) names nothing.
_FIELD = (
    r"(?!(?:None|null|nil)(?![^\s\"',)]))"
    r"(?:\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|[^\s\"',)]+)"
)

# The shapes in which a line names a device or what is playing, read off every
# logger call that passes one — `test_every_logged_listening_shape_is_redacted_
# without_knowing_it` renders each of them and goes red on one these do not
# read — plus the daemons' own, as measured. Each named group is a value to
# label, by its kind: `bt` is a Bluetooth address and `bt_name` the device it
# names (they share one label), `bt_filter` the BT remote's name filter, the
# others a label kind of their own. Everything outside the groups stays,
# because it is what says what happened.
LISTENING_SHAPES = tuple(re.compile(pattern) for pattern in (
    # A Bluetooth device, its name before its address: the A2DP source and its
    # BlueALSA monitor (sources/bluetooth), and the BT remote (hardware).
    r"\b(?:(?:Device connected|Device disconnected|Discovered matching BT device"
    r"|Auto-paired BT device|Reconnected paired device|Removing bond"
    r"|Disconnecting BT remote device|BT HID device found|Ignoring stale evdev node): "
    r"|(?:Disconnect requested for|Disconnecting|Pairing/connect failed for) )"
    rf"(?P<bt_name>.+?) \((?P<bt>{_MAC_TEXT}|None)\)",
    r"\bending (?P<bt_name>.+?)'s session\b",
    r"\b(?:Monitoring BT HID device: |Monitor cancelled for )(?P<bt_name>.+)$",
    r"\bError monitoring BT HID device (?P<bt_name>.+?): ",
    r"\bBT HID device disconnected: (?P<bt_name>.+) \(",
    r"\bBT remote controller initialized \(filter=(?P<bt_filter>.*)\)$",
    r"\bStarting BT discovery for '(?P<bt_filter>.*)' devices",
    # A Mac streaming over ROC, by its Bonjour name (sources/mac), and the Mac
    # link's calibration pinging it (core/mac_link).
    rf"\b(?:Connected|Disconnected): (?P<sender>.+?) \({_IPV4}\)",
    rf"{_AT_MESSAGE}(?P<sender>[^:\n]+?) \({_IPV4}\) answers no ping",
    # go-librespot, naming the device that handed it a session, or whose
    # session Spotify refused — measured: `msg="accepted zeroconf from Mac
    # mini" username="p6xy"`, `msg="failed creating new session from Mac mini"
    # error="…"`. The refusal is also relayed whole by the Spotify source, at
    # error, with its msg and its error joined by ": " — so the name stops
    # there too.
    r"\b(?:(?:accepted|refused) zeroconf|failed creating new session) from "
    r"(?P<sender>(?:[^\"\\\n]|\\.)+?)(?=\"|: |$)",
    # The logrus fields of the daemons that write them. go-librespot's account
    # is measured (`username="p6xy"`, on every sign-in and zeroconf line);
    # Navidrome's scrobble lines (`msg="Now Playing" artist=… title=…`) are
    # read from its source, not yet from a unit.
    rf"(?<![\w.])username=(?P<account>{_FIELD})",
    rf"(?<![\w.])title=(?P<title>{_FIELD})",
    rf"(?<![\w.])(?:album)?[aA]rtist=(?P<artist>{_FIELD})",
    rf"(?<![\w.])album=(?P<album>{_FIELD})",
    # go-librespot, naming what it loaded — measured: `msg="loaded track
    # \"Parapluie\" (paused: false, …)"`, the title's quotes escaped by logfmt.
    r"\bloaded (?:track|episode) \\?\"(?P<title>.*?)\\?\"(?=\s\(|\"|$)",
    # A cover looked up from a track's text (shared/artwork_resolver.py, and
    # the Bluetooth and radio sources that call it).
    rf"\bCover (?:found|lookup) for (?P<title>{_REPR})",
    rf"\bArtist (?P<artist>{_REPR}) is iTunes\b",
    # Lyrics (core/lyrics, api/lyrics.py): "<artist> - <title>" up to the
    # reason after the last colon, because a title can hold a colon of its own.
    r"\bLyrics lookup (?:failed|unavailable) for (?P<track>.+): ",
    # A disc MusicBrainz named (sources/cd); its disc id, the lookup key, stays.
    r"\bMusicBrainz named \S+: (?P<album>.+)$",
    # A podcast episode (sources/podcast).
    r"\bEpisode found: (?!Unknown$)(?P<title>.+)$",
    # Radio: what Shazam heard, and what Radio France says is on air.
    r"\bShazam: (?!title cleared\b|nothing$)(?P<track>.+)$",
    r"\bShazam heard (?P<track>.+?)(?: \(right after a refusal: no verdict\))?, next try in ",
    r"\bRadio France: (?!no song$)(?P<track>.+)$",
    # The Music Library's artists, as Deezer is asked for their photo.
    r"\b(?:No Deezer artist matching |Artist photo for |Deezer did not answer for artist "
    r"|Deezer artist search failed for |Deezer artist search HTTP \S+ for "
    rf"|Deezer refused the search for )(?P<artist>{_REPR})",
    rf"\bDeezer answered .* for (?P<artist>{_REPR}), not a search result",
    r"\bNot a browsable artist: (?P<artist>.+)$",
    # A playlist someone named (sources/music_library/routes.py).
    r"\bNavidrome rejected playlist creation: (?P<title>.+)$",
))

# A Bluetooth address, written out or inside a BlueZ / BlueALSA object path
# (`/org/bluez/hci0/dev_4C_2E_…`). The written-out form has the shape of every
# MAC, a multiroom client's included, so it is taken only on a line about
# Bluetooth; the path form is never anything else.
_MAC = re.compile(
    r"(?<!\w)(?<![0-9A-Fa-f]:)(?P<colon>[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})"
    r"(?!\w)(?!:[0-9A-Fa-f])"
    r"|(?<=dev_)(?P<path>[0-9A-Fa-f]{2}(?:_[0-9A-Fa-f]{2}){5})(?![0-9A-Za-z])"
)
# A line from bluetoothd, BlueALSA, the Bluetooth source or the BT remote: the
# unit or the logger name is on the line, in every format the report reads.
_BT_LINE = re.compile(r"blue(?:tooth|z|alsa)|bt_remote|a2dp|avrcp|\bhci\d", re.IGNORECASE)
# What the Redactor already wrote, which a shape must not label a second time.
_LABEL = re.compile(
    r"(?:bt|sender|account|title|artist|album|track|client|zone|share|usb|wifi)-\d+"
    r"(?: \(local\))?|milo-wifi-\d+|bt-remote-filter|<wifi>|<global-ipv6>"
)
_NUMBERED_LABEL = re.compile(r"([a-z]+)-(\d+)")
BT_REMOTE_FILTER_LABEL = "bt-remote-filter"


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

    Every value is replaced in one pass over the original text, so a label
    already written is never matched again: a network called "WiFi" would
    otherwise turn the `wifi-2` of "Livebox-WiFi" into `wifi-3-2`.

    The values in `folded` (network names) are also matched in another case,
    but only where a host name is written — a `.local` name, or the value of a
    refused Host/Origin header — because a host name reaches a log folded to
    lowercase. Anywhere else a network called "Home" would rewrite every
    `/home/milo` of a traceback.

    What no known value covers is taken by shape: `LISTENING_SHAPES` for the
    devices and the listening, and a Bluetooth address on a line about
    Bluetooth (`_MAC`). A value found that way gets the next label of its kind
    in the order the report meets it, and keeps it for the rest of the report,
    so the phone that connected at 14:02 is the same `bt-2` when it leaves.
    That is why one Redactor serves one report. The addresses in `keep_macs`
    are the multiroom clients', which the report keeps on purpose.
    """

    MIN_LENGTH = 3

    def __init__(
        self,
        replacements: Optional[Dict[str, str]] = None,
        *,
        folded: Iterable[str] = (),
        keep_macs: Iterable[str] = (),
        bt_addresses: Optional[Dict[str, str]] = None,
    ):
        replacements = replacements or {}
        self._keep_macs = {mac.upper() for mac in keep_macs}
        # (kind, value) → label, for what a shape found and what the unit knew.
        self._found: Dict[Tuple[str, str], str] = {
            ("bt", address.upper()): label for address, label in (bt_addresses or {}).items()
        }
        self._count: Dict[str, int] = {}
        for label in [*replacements.values(), *self._found.values()]:
            numbered = _NUMBERED_LABEL.fullmatch(label)
            if numbered:
                kind, index = numbered.group(1), int(numbered.group(2))
                self._count[kind] = max(self._count.get(kind, 0), index)
        values = sorted(
            (
                value for value in replacements
                if value and len(value) >= self.MIN_LENGTH
            ),
            key=len,
            reverse=True,
        )
        folded = set(folded)
        self._labels = [replacements[value] for value in values]
        in_text, in_header = [], []
        for i, value in enumerate(values):
            word = rf"(?<!\w){re.escape(value)}"
            exact = rf"(?P<v{i}>{word}(?!\w))"
            in_text.append(exact)
            in_header.append(exact)
            if value in folded:
                in_text.append(rf"(?P<f{i}>(?i:{word})(?=\.local(?!\w)))")
                in_header.append(rf"(?P<f{i}>(?i:{word})(?!\w))")
        self._header = re.compile("|".join(in_header)) if values else None
        self._text = (
            re.compile("|".join([r"(?P<header>\buntrusted (?:Host|Origin) '[^'\n]*')", *in_text]))
            if values else None
        )

    def _label(self, match: "re.Match[str]") -> str:
        return self._labels[int(match.lastgroup[1:])]

    def _substitute(self, match: "re.Match[str]") -> str:
        if match.lastgroup == "header":
            return self._header.sub(self._label, match.group(0))
        return self._label(match)

    def _next_label(self, kind: str, value: str) -> str:
        label = self._found.get((kind, value))
        if label is None:
            self._count[kind] = self._count.get(kind, 0) + 1
            label = f"{kind}-{self._count[kind]}"
            self._found[(kind, value)] = label
        return label

    def _bt_label(self, address: str, name: Optional[str]) -> Optional[str]:
        """The device's label, read from its address — or from its name, when
        a line naming it alone came first: one phone, one label, whichever of
        the two the report met it by."""
        address = address.replace("_", ":").upper()
        if address in self._keep_macs:
            return None
        if ("bt", address) not in self._found and name:
            known = name if re.fullmatch(r"bt-\d+", name) else self._found.get(("bt", name))
            if known:
                self._found[("bt", address)] = known
        return self._next_label("bt", address)

    def _shape(self, match: "re.Match[str]") -> str:
        """The match with every named value replaced by its label, in place."""
        groups = match.groupdict()
        address = groups.get("bt")
        device = (
            self._bt_label(address, groups.get("bt_name"))
            if address and address != "None" else None
        )
        text, base, cursor, pieces = match.group(0), match.start(), 0, []
        for name in sorted(
            (name for name, value in groups.items() if value is not None),
            key=lambda name: match.start(name),
        ):
            start, end = match.span(name)
            value = match.group(name)
            quote = value[0] if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"" else ""
            inner = value[1:-1] if quote else value
            # A label already written, and an address shown where no name was
            # found (a Mac nothing answered for), stay as they are.
            if (
                not inner or _LABEL.fullmatch(inner) or re.fullmatch(_IPV4, inner)
                or (name == "bt" and device is None)
            ):
                continue
            if name == "bt":
                label = device
            elif name == "bt_filter":
                label = BT_REMOTE_FILTER_LABEL
            elif name == "bt_name":
                label = device or self._next_label("bt", inner)
                self._found.setdefault(("bt", inner), label)
            else:
                label = self._next_label(name, inner)
            pieces += [text[cursor:start - base], f"{quote}{label}{quote}"]
            cursor = end - base
        return "".join([*pieces, text[cursor:]])

    def _mac(self, match: "re.Match[str]", about_bluetooth: bool) -> str:
        address = (match.group("colon") or match.group("path")).replace("_", ":").upper()
        if address in self._keep_macs:
            return match.group(0)
        if ("bt", address) not in self._found and not (about_bluetooth or match.group("path")):
            return match.group(0)
        return self._next_label("bt", address)

    def __call__(self, text: str) -> str:
        text = strip_global_ipv6(strip_query_strings(text))
        about_a_network = bool(_WIFI_LINE.search(text))
        about_bluetooth = bool(_BT_LINE.search(text))
        if self._text is not None:
            text = self._text.sub(self._substitute, text)
        for shape in LISTENING_SHAPES:
            text = shape.sub(self._shape, text)
        text = _MAC.sub(lambda match: self._mac(match, about_bluetooth), text)
        return strip_wifi_names(text, about_a_network)

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
        ssid_labels: Dict[str, str],
        bt_devices: Optional[Dict[str, str]] = None,
        listening: Iterable[Tuple[str, str]] = (),
        bt_remote_filter: Optional[str] = None,
    ) -> "Redactor":
        """One map from every excluded value this unit holds to its label.

        `bt_devices` is address → name, as BlueZ lists them: each device's
        `bt-N` is assigned in address order and stands for its name and its
        address alike. `listening` is `state_values()` of the audio state: each
        value gets the next label of its kind, unless it is already one of the
        names above (the phone the Bluetooth session names is its `bt-N`).
        """
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
        ssids = [ssid for ssid in ssid_labels if not _APPLIANCE_NAME.fullmatch(ssid)]
        for ssid in ssids:
            replacements[ssid] = ssid_labels[ssid]

        keep_macs = {mac.upper() for mac in client_labels}
        devices = {
            address.upper(): name for address, name in (bt_devices or {}).items()
            if address.upper() not in keep_macs
        }
        bt_addresses = {
            address: f"bt-{index}" for index, address in enumerate(sorted(devices), start=1)
        }
        # Never the appliance's own name: a speaker paired back to its server
        # would turn every milo-backend into bt-1-backend.
        for address, name in devices.items():
            if name and not _APPLIANCE_NAME.fullmatch(name):
                replacements.setdefault(name, bt_addresses[address])
        if bt_remote_filter:
            replacements.setdefault(bt_remote_filter, BT_REMOTE_FILTER_LABEL)
        counts: Dict[str, int] = {}
        for kind, value in listening:
            # A number names nobody, and as a whole word it would rewrite
            # every status code, year and port in the logs ("404", "1989").
            if (
                value in replacements or len(value) < cls.MIN_LENGTH or value.isdigit()
                or _APPLIANCE_NAME.fullmatch(value)
            ):
                continue
            counts[kind] = counts.get(kind, 0) + 1
            replacements[value] = f"{kind}-{counts[kind]}"
        return cls(
            replacements, folded=ssids, keep_macs=keep_macs, bt_addresses=bt_addresses
        )
