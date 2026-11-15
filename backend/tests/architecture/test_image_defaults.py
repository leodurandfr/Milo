"""Structural guardrail over what the flashed image ships as its defaults.

`pi-gen/config` is a third deployment tree in invariant #2's sense: it is
copied into a cloned pi-gen checkout, often inside Docker, and nothing in this
repo can reach it afterwards. Nothing in CI builds an image either, so every
value in that file is a decision no test could see — which is how it came to
carry `ENABLE_SSH=1` with a password identical on every unit Milō would ship,
and a timezone and WiFi country pinned to France for a product sold in eight
languages.

Five rules, each one the load-bearing half of a decision made elsewhere:

  1. **SSH is off in the image.** The factory password is the same on every
     unit, which is safe only while it stays a *local* convenience — SSH is the
     one remote path that accepts it, since neither nginx nor the backend
     authenticates anything. SSH opens only when the owner asks, from the UI,
     with a warning while the factory password stands; this is the other half,
     and without it that choice is made for every owner by a freshly flashed card.

  2. **The shipped timezone is the backend's `DEFAULT_TIMEZONE`.** The frontend
     adopts the browser's zone only while the system still sits on the value
     that means "nobody has told us yet" (`is_default`). Two spellings of that
     value is not a mismatch that errors — it is adoption that never fires, on
     every unit, silently, leaving the fleet on whatever the image baked.

  3. **No `WPA_COUNTRY`.** The regulatory domain travels through cmdline.txt
     (`provisioning/boot-common.sh`, `00`) and is set for real by
     `milo-set-wifi-country`. A second declaration here is a second answer to
     "where is this unit", and the wizard already refuses to continue without
     the real one.

  4. **The first-boot user wizard stays off.** The `milo` account is created by
     the image and everything is written around its name; upstream's
     export-image renames it on first boot unless told not to, by re-enabling
     `userconfig.service`, whose tty8 prompt nobody can answer on an appliance.

  5. **No cloud-init on the card.** Raspberry Pi Imager's OS customization
     reaches a trixie card through it — renaming the account, rewriting the
     hostname — and stage2 installs it whatever `ENABLE_CLOUD_INIT` says, so
     the stage purges it; `netplan.io`, which network-manager depends on, stays.

Plus the doctrine every guardrail here follows: the extractor asserts its own
output is non-trivial first, so a parse that stops matching fails loudly
instead of passing on an empty file.
"""
import re
from pathlib import Path

import pytest

from backend.api.system import DEFAULT_TIMEZONE

REPO_ROOT = Path(__file__).resolve().parents[3]
PIGEN_CONFIG = REPO_ROOT / "pi-gen" / "config"
STAGE_DIR = REPO_ROOT / "pi-gen" / "stage-milo"
SET_PASSWORD_HELPER = REPO_ROOT / "rootfs" / "usr" / "local" / "bin" / "milo-set-password"

# `NAME=value` / `NAME="value"`, skipping comment lines. pi-gen sources this
# file, so the assignments are the whole content.
ASSIGNMENT = re.compile(r'^([A-Z_][A-Z0-9_]*)=(.*)$')

# `apt-get [opts] purge|remove pkg… [2>/dev/null || true]`, in any stage script.
REMOVAL = re.compile(r'\bapt-get\s+(?:-\S+\s+)*(purge|remove)\s+(.*)')
SHELL_OPERATOR = re.compile(r'^(?:\d?>|\|\||&&|;|\|)')


def _config() -> dict:
    values = {}
    for line in PIGEN_CONFIG.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = ASSIGNMENT.match(stripped)
        if match:
            values[match.group(1)] = match.group(2).strip().strip('"')
    return values


def _stage_removals() -> dict:
    """Package name → the apt-get verbs the stage removes it with."""
    removals = {}
    for script in sorted(STAGE_DIR.glob("*/*.sh")):
        for line in script.read_text(encoding="utf-8").replace("\\\n", " ").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            match = REMOVAL.search(stripped)
            if not match:
                continue
            for token in match.group(2).split():
                if SHELL_OPERATOR.match(token):
                    break
                if not token.startswith("-"):
                    removals.setdefault(token, set()).add(match.group(1))
    return removals


@pytest.fixture(scope="module")
def config():
    values = _config()
    # Non-triviality: these four are what make the file a pi-gen config at all.
    # A parser that stopped matching would otherwise let every rule below pass
    # on an empty dict.
    for key in ("IMG_NAME", "RELEASE", "FIRST_USER_NAME", "STAGE_LIST"):
        assert key in values, f"{key} missing — the config parser matched nothing usable"
    return values


def test_the_image_ships_with_ssh_closed(config):
    """Rule 1. `ENABLE_SSH=1` puts a fleet-wide credential on port 22 of every
    unit sold, reachable by a scanner that never opens the UI."""
    assert config.get("ENABLE_SSH") == "0"


def test_the_first_boot_user_rename_is_disabled(config):
    """Rule 4. At `0`, upstream's export-image runs `rename-user -f -s` after
    stage-milo, which re-enables `userconfig.service`: its tty8 wizard waits for
    an answer forever, `multi-user.target` never completes, and the system stays
    `starting` — measured on a satellite flashed from the image."""
    assert config.get("DISABLE_FIRST_BOOT_USER_RENAME") == "1"


def test_cloud_init_is_not_seeded(config):
    """Rule 5. At `1`, stage2 seeds `user-data`, `meta-data` and
    `network-config` on the boot partition for a cloud-init the stage then
    purges: files on every card that read as the way to set its account and
    network, and do nothing."""
    assert config.get("ENABLE_CLOUD_INIT") == "0"


def test_the_stage_purges_cloud_init_and_keeps_netplan():
    """Rule 5, the half the config cannot do: stage2's package list installs
    cloud-init regardless, so a stage that stopped purging it ships it again with
    nothing failing. Purge rather than remove, or its conffiles under /etc/cloud
    stay behind. netplan.io must never be in a removal: network-manager depends
    on it, so apt would take the unit's whole network stack with it."""
    removals = _stage_removals()
    # Non-triviality: the ALSA-only removal of pulseaudio has always been there,
    # so a parser that reads nothing fails here rather than below.
    assert "pulseaudio" in removals, "no apt-get removal parsed from pi-gen/stage-milo"

    for package in ("cloud-init", "rpi-cloud-init-mods"):
        assert "purge" in removals.get(package, set()), f"the stage no longer purges {package}"
    assert "netplan.io" not in removals, "the stage removes netplan.io, and network-manager with it"


def test_the_first_user_is_still_milo(config):
    """Not a preference: /home/milo/milo, both sudoers policies and every
    systemd unit are written around this name, and `milo-set-password` sets the
    password of this account by name."""
    assert config.get("FIRST_USER_NAME") == "milo"


def test_the_shipped_timezone_is_the_one_the_backend_calls_default(config):
    """Rule 2. The two must be the same string, or the browser adoption never
    fires and every unit keeps the image's zone without a word."""
    assert config.get("TIMEZONE_DEFAULT") == DEFAULT_TIMEZONE


def test_no_wifi_country_is_pinned_in_the_image(config):
    """Rule 3."""
    assert "WPA_COUNTRY" not in config


def test_the_password_helper_reads_stdin_and_never_an_argument(config):
    """The password must not reach the helper through argv: /proc/<pid>/cmdline
    is world-readable, so an argument publishes it to every process on the box.

    Asserted on the helper as well as on the route that calls it, because the
    helper is what a future caller would reach for — and the two failures look
    identical from the outside (a password that works).
    """
    source = SET_PASSWORD_HELPER.read_text(encoding="utf-8")

    assert "read -r PASSWORD" in source, "the helper no longer reads the password from stdin"
    body = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "$1" not in body, "the helper takes the password as an argument"
