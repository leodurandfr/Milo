"""Structural guardrail: the audio path's silence and scheduling fixes stay in force.

Two silent failures were measured on this appliance and fixed in files no test
read, so either fix could regress with CI green and ship to every unit by OTA.

The silence pause. CamillaDSP is configured twice from this checkout —
`rootfs/var/lib/milo/camilladsp/config.yml` on the server and
`milo-client/configs/camilladsp/config.yml` on every satellite. `35615a88` added
`silence_threshold: -90` / `silence_timeout: 10` to the server's file alone, and
the two `devices` sections diverged for 44 days with CI green, until
`c9ba7763`. A pause stops the I2S bit clock: the PCM512x soft-mutes and ramps
back in on the first note after resume, and the rate-adjust loop has to
re-converge for 10 to 20 s — without a single journal line. The only tell is
`/proc/asound/.../status` reading PAUSED instead of RUNNING.

The realtime policy. The five audio daemons (CamillaDSP and snapclient on both
sides, snapserver on the server) run SCHED_FIFO. Measured 2026-07-22 under CPU
load: with snapserver back on SCHED_OTHER, onResync fired about 30 times as
often as with FIFO, and every room jerked in lockstep. A rewrite of an
`ExecStart=` that dropped `CPUSchedulingPolicy=fifo` would bring that back on
the whole fleet, and nothing would fail.

The twin mechanism of `test_rootfs_deployment.py` does not apply: the units
differ on purpose (user, `BindsTo=` vs `PartOf=`, the satellite's `sh -c`
wrapper), and the satellite's config lives outside `milo-client/rootfs/`. So the
shared *values* are named here. `test_eq_response.py` keeps its own role
(`samplerate` against the curve's `SAMPLERATE`) and is not restated.

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing on
an empty surface.
"""
from pathlib import Path

import pytest
import yaml

from backend.tests.architecture.test_unit_hardening import service_section

REPO_ROOT = Path(__file__).resolve().parents[3]

DSP_CONFIGS = {
    "server": REPO_ROOT / "rootfs" / "var" / "lib" / "milo" / "camilladsp" / "config.yml",
    "milo-client": REPO_ROOT / "milo-client" / "configs" / "camilladsp" / "config.yml",
}

AUDIO_UNITS = {
    "server-camilladsp": REPO_ROOT / "system" / "milo-camilladsp.service",
    "server-snapclient": REPO_ROOT / "system" / "milo-snapclient-multiroom.service",
    "server-snapserver": REPO_ROOT / "system" / "milo-snapserver-multiroom.service",
    "client-camilladsp": REPO_ROOT / "milo-client" / "system" / "milo-client-camilladsp.service",
    "client-snapclient": REPO_ROOT / "milo-client" / "system" / "milo-client-snapclient.service",
}

# The kernel's threaded IRQ handlers run SCHED_FIFO 50: an audio daemon above
# them would starve the very interrupts that feed it.
KERNEL_IRQ_PRIORITY = 50


def _devices(config: Path) -> dict:
    """The `devices` section of a CamillaDSP config, checked to be a real one."""
    devices = yaml.safe_load(config.read_text(encoding="utf-8")).get("devices")
    assert isinstance(devices, dict), f"{config}: no `devices` section — the extractor is broken"
    missing = {"samplerate", "capture", "playback"} - devices.keys()
    assert not missing, f"{config}: `devices` lacks {sorted(missing)} — the extractor is broken"
    return devices


def _realtime(unit: Path) -> dict[str, str]:
    """The last value of each scheduling directive in a unit's `[Service]` section."""
    directives = service_section(unit)
    assert "ExecStart" in directives, f"{unit}: no ExecStart= read — the extractor is broken"
    return {
        key: directives[key][-1]
        for key in ("CPUSchedulingPolicy", "CPUSchedulingPriority", "LimitRTPRIO", "LimitMEMLOCK")
        if key in directives
    }


def _fifo_units() -> set[Path]:
    """Every shipped unit, server or satellite, that asks for SCHED_FIFO."""
    units = {
        unit
        for tree in (REPO_ROOT / "system", REPO_ROOT / "milo-client" / "system")
        for unit in tree.glob("*.service")
        if service_section(unit).get("CPUSchedulingPolicy", [""])[-1] == "fifo"
    }
    assert units, "no shipped unit asks for SCHED_FIFO — the extractor is broken"
    return units


def _priority(policy: dict[str, str], unit: str) -> int:
    assert "CPUSchedulingPriority" in policy, f"{unit} sets no CPUSchedulingPriority="
    return int(policy["CPUSchedulingPriority"])


def test_both_dsp_configs_share_one_devices_section():
    """The server and the satellites never pause CamillaDSP on silence.

    Fails when one config gains a silence pause (or any other device setting)
    the other lacks — the `35615a88` drift, which cut the I2S clock on one side
    with nothing in any log.
    """
    sections = {side: _devices(path) for side, path in DSP_CONFIGS.items()}

    assert sections["server"] == sections["milo-client"], (
        "the server's and the satellite's CamillaDSP `devices` sections differ"
    )
    for side, devices in sections.items():
        assert devices.get("silence_timeout") == 0, (
            f"{side}: silence_timeout is {devices.get('silence_timeout')!r}, not 0 — "
            "a silence pause stops the I2S clock"
        )
        assert "silence_threshold" not in devices, f"{side}: a silence_threshold is set"


def test_the_realtime_units_are_exactly_the_audio_units():
    """A unit that gains or loses SCHED_FIFO is noticed here, not left unchecked.

    Fails when a new daemon is made realtime without the bounds below applying
    to it, or when one of the five drops FIFO and this list still names it.
    """
    assert _fifo_units() == set(AUDIO_UNITS.values())


@pytest.mark.parametrize("name", sorted(AUDIO_UNITS))
def test_every_audio_unit_runs_sched_fifo(name):
    """Each audio daemon is realtime, below the kernel's IRQ threads.

    Fails when a unit loses its FIFO policy (the onResync storm measured
    2026-07-22), asks for a priority its own `LimitRTPRIO=` refuses, or loses
    `LimitMEMLOCK=infinity`.
    """
    policy = _realtime(AUDIO_UNITS[name])

    assert policy.get("CPUSchedulingPolicy") == "fifo", (
        f"{name} does not run SCHED_FIFO: {policy.get('CPUSchedulingPolicy')!r}"
    )
    priority = _priority(policy, name)
    assert "LimitRTPRIO" in policy, f"{name} sets no LimitRTPRIO="
    assert 0 < priority <= int(policy["LimitRTPRIO"]), (
        f"{name}: priority {priority} outside 1..LimitRTPRIO={policy['LimitRTPRIO']}"
    )
    assert priority < KERNEL_IRQ_PRIORITY, (
        f"{name}: priority {priority} sits at or above the kernel IRQ threads"
    )
    assert policy.get("LimitMEMLOCK") == "infinity", (
        f"{name}: LimitMEMLOCK is {policy.get('LimitMEMLOCK')!r}, not infinity"
    )


def test_realtime_priorities_order_dsp_over_client_over_server():
    """CamillaDSP outranks snapclient, which outranks snapserver, and twins match.

    The output stage is the last to starve: what feeds it can be preempted for
    it, never the reverse. A satellite running its pair at other priorities than
    the server's would make the two sides' measurements incomparable.
    """
    priorities = {name: _priority(_realtime(unit), name) for name, unit in AUDIO_UNITS.items()}

    assert (
        priorities["server-camilladsp"]
        > priorities["server-snapclient"]
        > priorities["server-snapserver"]
    ), f"server priorities out of order: {priorities}"
    assert priorities["client-camilladsp"] > priorities["client-snapclient"], (
        f"satellite priorities out of order: {priorities}"
    )
    assert priorities["server-camilladsp"] == priorities["client-camilladsp"]
    assert priorities["server-snapclient"] == priorities["client-snapclient"]
