# backend/core/system/diagnostic/collectors.py
"""One function per section of the report.

Each returns rendered text and is allowed to fail: the caller turns an exception
or a timeout into a NOT COLLECTED heading and carries on, because the report is
generated precisely when parts of the appliance are not answering.

What is in here and what is not was chosen against this repository's own history
of real faults rather than against a notion of completeness. Three of them decide
the shape: CamillaDSP's silence pause leaves no trace in any log and shows only
as PAUSED in /proc/asound; an IR or rotary press logs nothing at all, so the only
evidence a control works is elsewhere in the system; and `journalctl -p warning`
is always empty here because Milō's own level lives in the message text, not in
the syslog priority. So the value is not in any one log — it is in several states
read at the same instant, which is what a snapshot is for.
"""
import logging
import platform
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config.constants import (
    HARDWARE_FILE,
    MUSIC_LIBRARY_DATA_FILE,
    MUSIC_LIBRARY_MOUNT_ROOT,
    NAVIDROME_URL,
)
from backend.core.models.audio_state import AudioSource
from backend.core.system.diagnostic import probes
from backend.core.system.diagnostic.render import cap_line
from backend.core.system.diagnostic.whitelist import (
    CLIENT_ALLOWED,
    EQUALIZER_ALLOWED,
    HARDWARE_ALLOWED,
    LISTEN_PORTS,
    SETTINGS_ALLOWED,
    SHARE_ALLOWED,
    USB_ALLOWED,
    ZONE_ALLOWED,
    project,
    strip_global_ipv6,
)

logger = logging.getLogger(__name__)

ENV_FILES = ("routing.env", "mac.env", "snapclient.env")


# --------------------------------------------------------------------------- #
# Small shared shaping helpers
# --------------------------------------------------------------------------- #

def _kv(pairs: Dict[str, Any], indent: str = "") -> List[str]:
    if not pairs:
        return [f"{indent}(none)"]
    width = max(len(k) for k in pairs)
    return [f"{indent}{k:<{width}} : {_fmt(v)}" for k, v in pairs.items()]


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else "(empty)"
    return str(value)


def _flatten(data: Dict[str, Any], allowed, prefix: str = "") -> Dict[str, Any]:
    """Every allowed dotted path present in `data`, flattened for display.

    A `*` segment in an allowed path stands for a key whose NAME is data (a MAC,
    a program key). Nothing outside `allowed` can come out: this walks the
    whitelist, not the input.
    """
    out: Dict[str, Any] = {}
    for path in sorted(allowed):
        if not path.startswith(prefix):
            continue
        cursor: Any = data
        segments = path.split(".")
        for i, segment in enumerate(segments):
            if not isinstance(cursor, dict):
                cursor = None
                break
            if segment == "*":
                # The remaining segments apply to every child; the only shapes
                # in this tree are a flat map (forced_versions) or a leaf.
                rest = segments[i + 1:]
                for key, child in cursor.items():
                    value = child
                    for deeper in rest:
                        value = value.get(deeper) if isinstance(value, dict) else None
                    if value is not None:
                        out[".".join(segments[:i]) + f".{key}"] = value
                cursor = None
                break
            cursor = cursor.get(segment)
        if cursor is not None:
            out[path] = cursor
    return out


# --------------------------------------------------------------------------- #
# 1. Identity
# --------------------------------------------------------------------------- #

async def identity(ctx) -> str:
    from backend.core.updates.catalog import PROGRAMS

    repo = PROGRAMS["milo"]["git_path"]
    uptime_raw, model, os_release = await probes.read_files([
        Path("/proc/uptime"),
        Path("/proc/device-tree/model"),
        Path("/etc/os-release"),
    ])
    kernel, exact_tag, describe = await _identity_commands(repo)

    uptime_s = int(float(uptime_raw.split()[0])) if uptime_raw else None
    pretty = "-"
    for line in (os_release or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            pretty = line.split("=", 1)[1].strip('"')

    settings = await _settings(ctx)
    return "\n".join(_kv({
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "hostname": platform.node(),
        "role": "server",
        "uptime": _duration(uptime_s),
        "timezone": _readlink_timezone(),
        "language": settings.get("language"),
        "model": (model or "-").strip("\x00 "),
        "os": pretty,
        "kernel": (kernel or "-").strip(),
        # A tree not sitting exactly on a tag is a development build and is
        # offered nothing by the update channel — the distinction the release
        # channel is built on, so the report states it rather than a version.
        "milo release": (exact_tag or "").strip() or "development build (not on a tag)",
        "milo git describe": (describe or "-").strip(),
    }))


async def _identity_commands(repo: str):
    import asyncio
    return await asyncio.gather(
        probes.run(["uname", "-r"], timeout=3.0),
        probes.run(["git", "-C", repo, "describe", "--tags", "--exact-match"], timeout=5.0),
        probes.run(["git", "-C", repo, "describe", "--tags", "--always"], timeout=5.0),
    )


def _readlink_timezone() -> str:
    import os
    try:
        target = os.readlink("/etc/localtime")
    except OSError:
        return "-"
    prefix = "/usr/share/zoneinfo/"
    return target[len(prefix):] if target.startswith(prefix) else target


def _duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "-"
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    return f"{days}d {hours}h {rest // 60}m"


# --------------------------------------------------------------------------- #
# 2. Versions
# --------------------------------------------------------------------------- #

async def versions(ctx) -> str:
    """Installed vs pinned vs deliberately-forced, for every program Milō ships.

    Local commands only — never GitHub. The export has to work with the network
    cut, and a report that hangs on a release API when the fault IS the network
    is the one case it must not fail.
    """
    import asyncio
    from backend.core.updates.catalog import PROGRAMS

    if ctx.version_service is None:
        raise RuntimeError("version service unavailable")

    keys = list(PROGRAMS)
    results = await asyncio.gather(
        *(ctx.version_service.get_installed_version(key) for key in keys),
        return_exceptions=True,
    )
    forced = await ctx.version_service.get_forced_versions()

    lines = [f"{'program':<16} {'installed':<24} {'pinned':<12} forced"]
    for key, result in zip(keys, results):
        if isinstance(result, Exception):
            lines.append(f"{key:<16} (read failed: {result})")
            continue
        installed = ", ".join(f"{k}={v}" for k, v in result.get("versions", {}).items())
        lines.append(
            f"{key:<16} {installed or result.get('status', '?'):<24} "
            f"{PROGRAMS[key].get('validated_version', '-'):<12} {forced.get(key, '-')}"
        )
    if forced:
        lines.append("")
        lines.append(
            "a forced version is a deliberate trial past the manifest, kept until "
            "a bump catches up with it"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 3. Hardware
# --------------------------------------------------------------------------- #

async def hardware(ctx) -> str:
    import asyncio

    raw = await probes.read_file(HARDWARE_FILE)
    config: Dict[str, Any] = {}
    if ctx.hardware_service is not None:
        config = ctx.hardware_service.get_full_config()
    elif raw:
        import json
        config = json.loads(raw)

    temp, throttled, cpu_ram = await asyncio.gather(
        probes.run(["vcgencmd", "measure_temp"], timeout=5.0),
        probes.run(["vcgencmd", "get_throttled"], timeout=5.0),
        _cpu_and_ram(),
    )

    settings = await _settings(ctx)
    bt = settings.get("hardware", {}).get("bt_remote", {})
    fields = _flatten(config, HARDWARE_ALLOWED)
    fields.update(_flatten(settings, SETTINGS_ALLOWED, prefix="hardware."))
    fields.update(_flatten(settings, SETTINGS_ALLOWED, prefix="fan."))
    # The filter string itself is the user's; only whether one is set is ours.
    fields["hardware.bt_remote.device_name_filter"] = (
        "configured" if bt.get("device_name_filter") else "not configured"
    )
    fields["audio card missing"] = (
        ctx.hardware_service.get_missing_audio_card() if ctx.hardware_service else None
    )
    fields["soc temperature"] = (temp or "-").strip()
    # Raw hex, undecoded on purpose: the previous decoder read the "has
    # occurred" bits at 19-22 where the Pi sets them at 16-19, and reported an
    # under-voltage this unit never had while hiding the three events it did.
    fields["vcgencmd get_throttled"] = (throttled or "-").strip()
    fields.update(cpu_ram)
    return "\n".join(_kv(fields))


async def _cpu_and_ram() -> Dict[str, Any]:
    meminfo = await probes.read_file(Path("/proc/meminfo"))
    loadavg = await probes.read_file(Path("/proc/loadavg"))
    out: Dict[str, Any] = {"load average": (loadavg or "-").split(" up ")[0].strip()}
    if meminfo:
        values = {}
        for line in meminfo.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                values[parts[0].rstrip(":")] = int(parts[1])
        total, available = values.get("MemTotal", 0), values.get("MemAvailable", 0)
        if total:
            out["ram"] = f"{(total - available) // 1024} MB used of {total // 1024} MB"
    return out


# --------------------------------------------------------------------------- #
# 4. Audio path — the combination that no single log shows
# --------------------------------------------------------------------------- #

async def audio_path(ctx) -> str:
    import asyncio

    cards, mixer, alsa_states, envs = await asyncio.gather(
        probes.read_file(Path("/proc/asound/cards")),
        probes.run(["amixer", "-c", "0", "scontents"], timeout=5.0),
        _alsa_stream_states(),
        _env_files(),
    )

    lines = ["--- ALSA cards ---"]
    lines += [cap_line(line) for line in (cards or "(unreadable)").rstrip().splitlines()]

    lines += ["", "--- ALSA streams (RUNNING vs PAUSED vs closed) ---"]
    lines += alsa_states or ["(no pcm status files)"]

    lines += ["", "--- card mixer ---"]
    # CamillaDSP is the only attenuation stage; the card's own mixer is pinned at
    # unity by milo-alsa-passthrough on every boot. A control that has drifted
    # off 0.00dB is a fault this is the only view of.
    lines += _mixer_playback_lines(mixer)

    lines += ["", "--- CamillaDSP ---"]
    lines += await _camilladsp_lines(ctx)

    lines += ["", "--- routing ---"]
    routing = ctx.routing_service.get_state() if ctx.routing_service else {}
    lines += _kv({k: v for k, v in routing.items()})
    for name, content in envs.items():
        for line in content.splitlines():
            if line.strip() and not line.startswith("#"):
                lines.append(f"{name}: {line.strip()}")

    lines += ["", "--- snapcast ---"]
    lines += await _snapcast_lines(ctx)
    return "\n".join(lines)


async def _alsa_stream_states() -> List[str]:
    """Every pcm substream's state, in one pass.

    This is where CamillaDSP's silence pause is visible and nowhere else: it
    writes nothing to any log, and the only difference between a healthy path
    and a silently paused one is RUNNING vs PAUSED in this file.
    """
    import asyncio

    paths = sorted(Path("/proc/asound").glob("card*/pcm*/sub*/status"))
    contents = await asyncio.gather(*(probes.read_file(p) for p in paths))
    lines = []
    closed: Dict[str, int] = {}
    for path, content in zip(paths, contents):
        if content is None:
            lines.append(f"{path}: (unreadable)")
            continue
        body = content.split()
        if body and body[0] == "closed":
            # Summarised, not listed: three loopback cards make 60-odd idle
            # substreams, and burying the two that are RUNNING in that list is
            # the opposite of what this section is for.
            device = str(path.parent.parent)
            closed[device] = closed.get(device, 0) + 1
            continue
        fields = dict(
            (k.strip(), v.strip())
            for k, v in (line.split(":", 1) for line in content.splitlines() if ":" in line)
        )
        lines.append(
            f"{path}: {fields.get('state', '?')} "
            f"delay={fields.get('delay', '?')} avail={fields.get('avail', '?')} "
            f"avail_max={fields.get('avail_max', '?')}"
        )
    if closed:
        lines.append(
            "closed: " + ", ".join(f"{d} ({n})" for d, n in sorted(closed.items()))
        )
    return lines


def _mixer_playback_lines(mixer: Optional[str]) -> List[str]:
    if not mixer:
        return ["(amixer unavailable)"]
    control = None
    out = []
    for line in mixer.splitlines():
        if line.startswith("Simple mixer control"):
            control = line.split("'")[1] if "'" in line else line
        elif "Playback" in line and "dB" in line and control:
            out.append(cap_line(f"{control}: {line.strip()}"))
    return out or ["(no playback level reported by this card)"]


async def _env_files() -> Dict[str, str]:
    from backend.config.constants import MILO_DATA_DIR

    contents = await probes.read_files([MILO_DATA_DIR / name for name in ENV_FILES])
    return {name: text for name, text in zip(ENV_FILES, contents) if text}


async def _camilladsp_lines(ctx) -> List[str]:
    active = await probes.is_active("milo-camilladsp.service")
    fields: Dict[str, Any] = {"milo-camilladsp.service": "active" if active else "not active"}
    service = ctx.camilladsp_service
    if service is None:
        fields["state"] = "service not wired in this process"
        return _kv(fields)
    fields["connected"] = service.connected
    fields["daemon state"] = getattr(service.state, "value", service.state)
    fields["effects enabled"] = service.effects_enabled
    fields["volume control available"] = service.is_volume_control_available()
    if service.connected:
        try:
            volume = await service.get_volume()
            fields["volume dB"] = volume.get("volume")
            fields["muted"] = volume.get("muted")
        except Exception as e:
            fields["volume dB"] = f"(read failed: {e})"
    return _kv(fields)


async def _snapcast_lines(ctx) -> List[str]:
    fields: Dict[str, Any] = {}
    if ctx.routing_service is not None:
        fields.update(await ctx.routing_service.get_snapcast_status())
    if ctx.snapcast_service is not None:
        try:
            fields.update(await ctx.snapcast_service.get_server_config())
        except Exception as e:
            fields["server config"] = f"(unreadable: {e})"
    return _kv(fields)


# --------------------------------------------------------------------------- #
# 5. Audio sources
# --------------------------------------------------------------------------- #

async def sources(ctx) -> str:
    """Every source's state and its unit — never its metadata.

    Metadata is where the Music Library's file paths and every track title live,
    and none of that says anything about a fault.
    """
    lines = []
    if ctx.state_machine is not None:
        state = ctx.state_machine.get_current_state()
        lines += _kv({
            "active source": state.get("active_source"),
            "source state": state.get("source_state"),
            "transitioning": state.get("transitioning"),
            "error": state.get("error"),
            "multiroom enabled": state.get("multiroom_enabled"),
            "equalizer effects": state.get("equalizer_effects_enabled"),
            "network unavailable": state.get("network_unavailable"),
        })
        lines.append("")
        per_source = {}
        for source in AudioSource:
            if source is AudioSource.NONE:
                continue
            instance = ctx.state_machine.get_source(source)
            if instance is None:
                continue
            per_source[source.value] = getattr(instance.state, "value", instance.state)
        lines += _kv(per_source)
    else:
        lines.append("(state machine not wired in this process)")

    lines += ["", "--- units ---"]
    units = await probes.list_milo_units("milo-*")
    lines += await probes.unit_summary(units)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 6. Multiroom
# --------------------------------------------------------------------------- #

async def multiroom(ctx) -> str:
    if ctx.registry_service is None:
        raise RuntimeError("client registry unavailable")

    lines = ["--- clients (registry) ---"]
    clients = ctx.registry_service.get_all_clients()
    for mac in sorted(clients):
        client = clients[mac]
        fields = project(client.to_dict(), CLIENT_ALLOWED)
        fields["zone_id"] = ctx.zone_labels.get(fields.get("zone_id")) or "-"
        lines.append(f"{ctx.labels.get(mac, mac)}:")
        lines += _kv(fields, indent="  ")

    lines += ["", "--- zones ---"]
    zones = ctx.registry_service.get_all_zones()
    if not zones:
        lines.append("(none)")
    for zone_id in sorted(zones):
        zone = zones[zone_id]
        fields = project(zone.to_dict(), ZONE_ALLOWED)
        fields["client_ids"] = [ctx.labels.get(m, m) for m in fields.get("client_ids", [])]
        lines.append(f"{ctx.zone_labels.get(zone_id, zone_id)}:")
        lines += _kv(fields, indent="  ")

    lines += ["", "--- snapserver's own view ---"]
    lines += await _snapserver_clients(ctx)

    lines += ["", "--- per-client equalizer ---"]
    for mac in sorted(clients):
        settings = ctx.registry_service.get_client_equalizer(mac)
        if settings is None:
            continue
        record = project(settings.to_dict(), EQUALIZER_ALLOWED)
        gains = [f["gain"] for f in record.get("filters", [])]
        lines.append(
            f"{ctx.labels.get(mac, mac)}: enabled={_fmt(record.get('enabled'))} "
            f"preset={record.get('active_preset')} mono={_fmt(record.get('mono'))} "
            f"compressor={_fmt(record.get('compressor', {}).get('enabled'))} "
            f"loudness={_fmt(record.get('loudness', {}).get('enabled'))}"
        )
        lines.append(f"  gains: {', '.join(f'{g:+.1f}' for g in gains)}")
    return "\n".join(lines)


async def _snapserver_clients(ctx) -> List[str]:
    """What snapserver believes, beside what the registry believes.

    Two views of one fleet: a client the registry calls online that snapserver
    does not list, or the reverse, is a whole class of multiroom fault and is
    invisible from either side alone.
    """
    if ctx.snapcast_service is None:
        return ["(snapcast service not wired in this process)"]
    try:
        clients = await ctx.snapcast_service.get_clients()
    except Exception as e:
        return [f"(snapserver did not answer: {e})"]
    if not clients:
        return ["(snapserver lists no client)"]

    lines = []
    for client in clients:
        mac = client.get("mac_id", "?")
        # `name` is skipped: snapserver carries the same user-chosen room name
        # the registry does, and this view exists for the level and the address.
        lines.append(
            f"{ctx.labels.get(mac, mac)}: volume={client.get('volume')} "
            f"muted={_fmt(client.get('muted'))} ip={client.get('ip')} "
            f"host={client.get('host')} last_seen_age={client.get('last_seen_age')}s"
        )
    lines.append("(snapserver reports online clients only — an absence here is a departure)")
    return lines


# --------------------------------------------------------------------------- #
# 7. Storage
# --------------------------------------------------------------------------- #

async def storage(ctx) -> str:
    """Configured storage, whether it is mounted, and whether it still answers.

    The liveness probe is the point. A share whose server went away stays
    mounted, Navidrome keeps serving its index, and a scan across it marks
    nothing — measured on this fleet with the cable cut for 627 s. Only a
    bounded stat on the mountpoint tells the two apart.
    """
    import asyncio
    import json

    raw = await probes.read_file(MUSIC_LIBRARY_DATA_FILE)
    data = json.loads(raw) if raw else {}
    mounts = await probes.read_file(Path("/proc/mounts")) or ""
    mounted_paths = {line.split()[1] for line in mounts.splitlines() if len(line.split()) > 1}

    lines = ["--- network shares ---"]
    shares = data.get("shares", [])
    if not shares:
        lines.append("(none)")
    probe_results = await asyncio.gather(*(
        probes.stat_mount(str(MUSIC_LIBRARY_MOUNT_ROOT / share["id"]))
        for share in shares if "id" in share
    ))
    for share, answer in zip(shares, probe_results):
        fields = project(share, SHARE_ALLOWED)
        path = str(MUSIC_LIBRARY_MOUNT_ROOT / share.get("id", ""))
        fields["mounted"] = path in mounted_paths
        # The distinction the rest of the appliance cannot make: a share whose
        # server went away stays mounted and keeps serving its stale index.
        fields["responds"] = answer or "NO — the server did not answer within 3 s"
        lines.append(f"{ctx.share_labels.get(share.get('id'), 'share-?')}:")
        lines += _kv(fields, indent="  ")

    lines += ["", "--- USB keys ever mounted ---"]
    known = data.get("known_usb", {})
    if not known:
        lines.append("(none)")
    for uuid in sorted(known):
        fields = project(known[uuid], USB_ALLOWED)
        lines.append(f"{ctx.usb_labels.get(uuid, 'usb-?')} ({uuid}):")
        lines += _kv(fields, indent="  ")

    lines += ["", "--- navidrome ---"]
    lines += await _navidrome_lines()
    return "\n".join(lines)


async def _navidrome_lines() -> List[str]:
    active = await probes.is_active("milo-navidrome.service")
    fields: Dict[str, Any] = {"milo-navidrome.service": "active" if active else "not active"}
    # Its own ping endpoint, unauthenticated, on loopback: enough to tell a dead
    # daemon from one that is merely busy indexing.
    reachable = await probes.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "--max-time", "3", f"{NAVIDROME_URL}/ping"],
        timeout=5.0,
    )
    fields["http"] = (reachable or "no answer").strip()
    return _kv(fields)


# --------------------------------------------------------------------------- #
# 8. Network
# --------------------------------------------------------------------------- #

async def network(ctx) -> str:
    """Addresses, routes and which of Milō's own ports answer.

    Deliberately not `ss -ltn` whole: that lists every unrelated listener on the
    box including the Tailscale address. The ports Milō owns are asked about by
    number instead.
    """
    import asyncio

    addr, route, resolv, listening = await asyncio.gather(
        probes.run(["ip", "-brief", "-4", "address"], timeout=3.0),
        probes.run(["ip", "-4", "route", "show", "default"], timeout=3.0),
        probes.read_file(Path("/etc/resolv.conf")),
        probes.run(["ss", "-ltnH"], timeout=3.0),
    )

    lines = ["--- interfaces ---"]
    lines += [cap_line(line) for line in (addr or "(unreadable)").rstrip().splitlines()]
    lines += ["", "--- default route ---", (route or "(none)").strip()]

    lines += ["", "--- dns ---"]
    # A resolver reached over IPv6 is usually the ISP's, and its address carries
    # the same subscriber identity as the unit's own global address.
    lines += [
        strip_global_ipv6(line.strip()) for line in (resolv or "").splitlines()
        if line.startswith("nameserver")
    ] or ["(none)"]

    lines += ["", "--- milo ports ---"]
    open_ports = set()
    for line in (listening or "").splitlines():
        parts = line.split()
        if len(parts) >= 4 and ":" in parts[3]:
            try:
                open_ports.add(int(parts[3].rsplit(":", 1)[1]))
            except ValueError:
                continue
    lines += _kv({
        f"{port} ({label})": "listening" if port in open_ports else "not listening"
        for port, label in sorted(LISTEN_PORTS.items())
    })

    lines += ["", "--- name and connectivity ---"]
    fields: Dict[str, Any] = {}
    if ctx.connectivity_service is not None:
        fields.update(ctx.connectivity_service.get_state())
    if ctx.hostname_conflict_service is not None:
        fields.update(ctx.hostname_conflict_service.get_state())
    settings = await _settings(ctx)
    fields["wifi country"] = settings.get("wifi", {}).get("country") or "(unset)"
    avahi = await probes.read_file(Path("/var/lib/milo/avahi-interface"))
    fields["avahi interface"] = (avahi or "-").strip()
    lines += _kv(fields)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 9. Settings
# --------------------------------------------------------------------------- #

async def settings_section(ctx) -> str:
    settings = await _settings(ctx)
    if not settings:
        raise RuntimeError("settings service unavailable")
    fields = _flatten(settings, SETTINGS_ALLOWED)
    # The hardware and fan blocks are rendered with the hardware they configure.
    fields = {
        k: v for k, v in fields.items()
        if not k.startswith("hardware.") and not k.startswith("fan.")
    }
    return "\n".join(_kv(fields))


async def _settings(ctx) -> Dict[str, Any]:
    if ctx.settings_service is None:
        return {}
    try:
        return await ctx.settings_service.get_all_settings()
    except Exception as e:
        logger.warning("diagnostic could not read settings: %s", e)
        return {}
