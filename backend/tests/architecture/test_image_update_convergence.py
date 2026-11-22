"""Structural guardrail: a unit updated to a release holds what a card flashed with it holds.

Two paths put a release's system on a unit. The image build
(`pi-gen/stage-milo/`) does it once, for every card flashed from it; every Milō
update does it again with `milo-deploy-update sync-system-files`, on every unit
already running. The sync used to install rootfs/ and system/ and nothing else,
while the stage also installed packages, wrote config.txt and cmdline.txt lines
and enabled units — so whatever a release added there reached new cards and no
running unit. Measured: libcairo2 joined the stage's package list in October
2026 (6ddc5a89) and every updated unit started the new release without it; the
trial failed its starts and the guard put the old release back, with no line
saying why. The nginx site's 150 s adoption timeout, the memory cgroup on the
kernel command line and the snd-aloop options without `index=` were image-only
the same way.

Now what a file cannot say is declared once, in `system-manifest.env`, and both
paths apply it: the stage installs its packages and runs
`milo-deploy-update apply-system-manifest` in its chroot, and the sync runs the
same functions on the unit before the reboot into the release. This file holds
the three ways that can come apart again:

  * the stage installs something the manifest does not declare and rootfs/ or
    system/ does not carry, or gives a rootfs/ file another mode than the sync
    gives it — the guardrail below, which reads every line of the stage and of
    the `provisioning/` functions it calls, and admits the rest only from a
    list where each entry says why no update has to install it;
  * the two paths stop reading the same declaration through the same code;
  * the sync's application of it stops being what a rollback can afford —
    additive, offline when nothing is missing, inside the guard's budget.

The convergence is for the server: a satellite is refreshed by a push of
`milo-client/` alone, and its system stays the image's.

Doctrine note: every extractor asserts its own output is non-trivial first, and
the guardrail is shown red against simulated drift before it is trusted green.
"""
import fnmatch
import os
import re
import shlex
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from backend.tests.architecture.test_provisioning_parity import CALL_RE, _function_bodies

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = REPO_ROOT / "system-manifest.env"
STAGE_DIR = REPO_ROOT / "pi-gen" / "stage-milo"
ROOTFS = REPO_ROOT / "rootfs"
DEPLOY = ROOTFS / "usr" / "local" / "bin" / "milo-deploy-update"
GUARD = ROOTFS / "usr" / "local" / "bin" / "milo-update-guard"
WIFI_COUNTRY = ROOTFS / "usr" / "local" / "bin" / "milo-set-wifi-country"

# Where the image's clone of this repo sits, which every stage line names.
CHECKOUT = "/home/milo/milo"

SHELL_FUNC_RE = re.compile(r"^([a-z0-9_]+)\(\) \{\n(.*?)^\}\n", re.MULTILINE | re.DOTALL)


# --------------------------------------------------------------------------- #
# The manifest, read as bash reads it.
# --------------------------------------------------------------------------- #

def _script_array(name: str) -> tuple[str, ...]:
    """A bash array milo-deploy-update declares, read from the script."""
    found = re.search(rf"^{name}=\(([^)]*)\)", DEPLOY.read_text(), re.MULTILINE)
    assert found, f"{name} not found in milo-deploy-update"
    return tuple(found.group(1).split())


def _manifest_arrays() -> tuple[str, ...]:
    """Every array milo-deploy-update reads: the frozen ones, then those added."""
    return _script_array("MANIFEST_ARRAYS") + _script_array("MANIFEST_ARRAYS_ADDED")


def _manifest(path: Path = MANIFEST) -> dict[str, list[str]]:
    names = _manifest_arrays()
    script = f'source "{path}"; ' + "; ".join(
        f'printf "%s\\0" "{name}" "${{#{name}[@]}}" "${{{name}[@]}}"' for name in names
    )
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout
    fields = out.split("\0")[:-1]
    arrays, i = {}, 0
    while i < len(fields):
        name, count = fields[i], int(fields[i + 1])
        arrays[name] = fields[i + 2:i + 2 + count]
        i += 2 + count
    return arrays


@pytest.fixture(scope="module")
def manifest() -> dict[str, list[str]]:
    arrays = _manifest()
    assert set(arrays) == set(_manifest_arrays()), f"arrays not read: {sorted(arrays)}"
    assert len(arrays["SYSTEM_APT_PACKAGES"]) >= 50, arrays["SYSTEM_APT_PACKAGES"]
    assert {"git", "nginx", "libcairo2"} <= set(arrays["SYSTEM_APT_PACKAGES"])
    assert "milo-backend.service" in arrays["SYSTEM_UNITS_ENABLED"]
    assert "cgroup_enable=memory" in arrays["SYSTEM_CMDLINE"]
    assert "dtparam=eee=off" in arrays["SYSTEM_CONFIG_TXT"]
    return arrays


# --------------------------------------------------------------------------- #
# What the stage does, line by line.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Site:
    where: str      # file, or provisioning function
    line: str       # the logical line: continuations joined, loop variables expanded
    kind: str       # what it installs: copy, apt, unit, write, mode, other


# A command's own position on a line — its start, or after an operator or a
# shell keyword — where `install` is the command and not an argument
# (`pip install`, `make install`).
_CMD = r"(?:^|[;&|({]\s*|\b(?:then|do|else)\s+)"
# A path of the target: absolute, or a variable naming one; never /tmp or
# /dev, nor the target's /tmp written to from the host.
_TARGET = r"\"?(?:/(?!tmp/|dev/)|\$\{?(?!ROOTFS_DIR\}?/tmp/)[A-Z][A-Z0-9_]*\b)"

# A file put in place from another: group 1 or 2 is the verb. `install -d`
# makes a directory and copies nothing.
_COPY_RE = re.compile(
    r"(?:^|[\s;&|({])(cp|mv|rsync)\s"
    + "|" + _CMD + r"(install)(?![^;&|]*\s-(?:d|-directory)\b)\s"
)
# A mode or an owner set on a path of the target: group 1 is the verb.
_MODE_RE = re.compile(r"(?:^|[\s;&|({])(chmod|chown|chgrp)\s+(?:-\w+\s+)*\S+\s+" + _TARGET)

# Each pattern names one way a line puts something on the target. A line can
# match several (a `cp` and a redirection); each is classified on its own.
_SITE_PATTERNS = (
    ("copy", _COPY_RE),
    ("apt", re.compile(r"\bapt(?:-get)?\s+(?:-\S+\s+(?:\S+=\S+\s+)?)*(?:install|remove|purge|autoremove)\b|\bapt-mark\b")),
    ("unit", re.compile(r"\bsystemctl\s+(?:-\S+\s+)*(?:enable|disable|mask|unmask|set-default|preset)\b")),
    ("write", re.compile(
        # A redirection onto a path of the target: not /dev/null, not /tmp, not
        # a file descriptor.
        r"(?<![<>&0-9])>>?\s*\"?(?!/dev/null|/tmp/|&)(?:/|\$\{?[A-Za-z_]\w*\}?/)\S*"
        r"|\btee\s+(?:-a\s+)?\"?(?:/|\$)"
        r"|\bsed\s+-i\b"
        r"|\bln\s+-\w*s"
        r"|\brm\s+(?:-\w+\s+)*\"?/(?:etc|boot|usr)/"
        # A download saved straight onto the target.
        r"|\bwget\b[^;&|]*\s(?:-O|--output-document)[\s=]*" + _TARGET
        + r"|\bcurl\b[^;&|]*\s(?:-o|--output)[\s=]*" + _TARGET
    )),
    ("mode", _MODE_RE),
    ("other", re.compile(
        r"\b(?:usermod|useradd|groupadd|plymouth-set-default-theme|update-initramfs"
        r"|debconf-set-selections|dpkg-reconfigure|raspi-config|update-alternatives)\b"
        r"|\bmake\s+install\b|\bscons\b.*\binstall\b|\bpip3?\b.*\binstall\b|/pip\s+install\b"
        r"|\bgit\s+clone\b|\bpython3\s+-m\s+venv\b|\bdpkg\s+-i\b"
        r"|\btar\s+[^|;]*-C\s+\"?(?!/tmp)\S"
        r"|\bunzip\s+[^|;]*-d\s+\"?(?!/tmp)\S"
        r"|\bpython3\s+[^;&|]*?\.py\b"
        # A helper of the tree, run by the stage: what it writes, no update runs.
        + "|" + _CMD + r"(?:sudo\s+(?:-\S+\s+)*)?(?:/usr/local/bin/)?milo-[a-z0-9.-]+(?=[\s;]|$)"
    )),
)

# The one stage line that runs a helper of the tree on purpose: the manifest.
_APPLY_MANIFEST = r"/usr/local/bin/milo-deploy-update apply-system-manifest"
_MANIFEST_PACKAGES = r"\binstall\s+-y\s+\$\{SYSTEM_APT_PACKAGES\[\*\]\}\s*$"


# A heredoc's operator, never a here-string's (`<<<`): group 2 is the delimiter.
_HEREDOC_RE = re.compile(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
# pi-gen's chroot wrapper, whatever the delimiter and its quoting (upstream's
# own stages spell it `on_chroot << EOF`).
_CHROOT_RE = re.compile(r"on_chroot\s*<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
# A heredoc that is a file's content: `tee` or `cat` writing it to a path of
# the target.
_WRITER_RE = re.compile(r"(?:sudo\s+(?:-\S+\s+)*)?(?:tee|cat)\b")
_WRITE_SITE_RE = dict(_SITE_PATTERNS)["write"]


def _logical_lines(text: str) -> list[str]:
    """Code lines, continuations joined, comments and the chroot wrapper
    dropped.

    A heredoc's body is code — the wrapper's (run in the chroot), a shell's
    (`sudo -u milo bash << 'SH'`), anything's — except the body that `tee` or
    `cat` writes to a path of the target, which is the file's content: its
    opener is itself a write site, judged like any other, so skipping that body
    hides nothing. A heredoc ends at the first line equal to its delimiter, or
    to the delimiter of one it sits in (the host's shell reads the outer one
    to its end without knowing what is inside). One never closed is a parse
    gone wrong, refused rather than read as the rest of the file skipped."""
    joined = re.sub(r"\\\n\s*", " ", text)
    lines: list[str] = []
    open_heredocs: list[tuple[str, bool]] = []  # (delimiter, is a file's content)
    for raw in joined.splitlines():
        line = raw.strip()
        closed = next((i for i, (delimiter, _) in enumerate(open_heredocs) if line == delimiter), None)
        if closed is not None:
            del open_heredocs[closed:]
            continue
        if open_heredocs and open_heredocs[-1][1]:
            continue
        if not line or line.startswith("#"):
            continue
        wrapper = _CHROOT_RE.fullmatch(line)
        if wrapper:
            open_heredocs.append((wrapper.group(2), False))
            continue
        lines.append(line)
        opened = _HEREDOC_RE.search(line)
        if opened:
            content = bool(_WRITER_RE.match(line) and _WRITE_SITE_RE.search(line))
            open_heredocs.append((opened.group(2), content))
    assert not open_heredocs, f"heredocs never closed: {[d for d, _ in open_heredocs]}"
    return lines


_FOR_RE = re.compile(r"\bfor\s+(\w+)\s+in\s+([^\s;]+)\s*(?:;|$)")


def _sites(where: str, text: str) -> list[Site]:
    """Every installing line, with what it names spelled out: a loop variable
    is the glob of the nearest `for` above it (the stage reuses names across
    loops over different trees), the basename of a glob is `*`, and
    $MILO_APP_DIR is the checkout."""
    bindings: dict[str, str] = {}
    sites = []
    for line in _logical_lines(text):
        loop = _FOR_RE.search(line)
        if loop:
            bindings[loop.group(1)] = loop.group(2).strip('"')
        for name, glob in bindings.items():
            line = re.sub(rf'"\$\{{?{name}\}}?"|\$\{{?{name}\}}?(?!\w)', lambda _, g=glob: g, line)
        line = re.sub(r'\$\(basename\s+"?([^")\s]+)"?\)', lambda m: m.group(1).rsplit("/", 1)[-1], line)
        line = re.sub(r"\$\{?MILO_APP_DIR\}?", CHECKOUT, line)
        sites += [Site(where, line, kind) for kind, pattern in _SITE_PATTERNS if pattern.search(line)]
    return sites


def _words(line: str, start: int) -> list[str] | None:
    """The words of the command starting at `start`, up to its operator."""
    segment = re.split(r"\s*(?:&&|\|\||;)\s*", line[start:])[0]
    try:
        return shlex.split(segment.replace("\\$", "$"))
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# What an update installs from the checkout, read from the deploy script.
# --------------------------------------------------------------------------- #

def _deploy_functions() -> dict[str, str]:
    return dict(SHELL_FUNC_RE.findall(DEPLOY.read_text()))


def _system_destinations() -> dict[str, str]:
    """{system/ file or glob: where the sync installs it}."""
    text = DEPLOY.read_text().replace("\\\n", " ")
    found = {}
    if re.search(r'for service_file in "\$REPO_PATH"/system/\*\.service', text) and \
            'install_atomic "$service_file" "/etc/systemd/system/$(basename "$service_file")"' in text:
        found["*.service"] = "/etc/systemd/system/"
    for name, dest in re.findall(r'install_atomic "\$REPO_PATH/system/([^"]+)"\s+(\S+)', text):
        found[name] = dest
    assert {"*.service", "avahi-daemon-override.conf"} <= set(found), (
        f"the sync's system/ destinations were not read: {found}"
    )
    return found


def _sync_modes() -> tuple[list[tuple[str, str]], list[str]]:
    """How the sync sets the mode of a rootfs/ file: the installers its case
    sends a path to, each with the mode it gives, and the destinations it
    makes executable. Every other file keeps its mode in git."""
    functions = _deploy_functions()
    body = functions["cmd_sync_system_files"]
    installers = []
    for pattern, name in re.findall(r'^\s+(\S+)\) (install_\w+) "\$rel_path";', body, re.MULTILINE):
        mode = re.search(r"chmod (\d{4}) |install_atomic \S+ \S+ (\+x|\d{4})\b", functions[name])
        assert mode, f"{name} sets no mode this test can read"
        installers.append((pattern, mode.group(1) or mode.group(2)))
    executable = re.search(r"^\s+(\S+)\) mode=\+x ;;$", body, re.MULTILINE)
    assert len(installers) >= 4 and executable, f"the sync's modes were not read: {installers}"
    return installers, executable.group(1).split("|")


SYSTEM_DESTINATIONS = _system_destinations()
SYNC_MODES = _sync_modes()
ROOTFS_FILES = sorted(str(p.relative_to(ROOTFS)) for p in ROOTFS.rglob("*") if p.is_file())


def _sync_mode(rel: str) -> int:
    installers, executable = SYNC_MODES
    tracked = 0o755 if (ROOTFS / rel).stat().st_mode & 0o100 else 0o644
    mode = next((m for pattern, m in installers if fnmatch.fnmatchcase(rel, pattern)), None)
    if mode == "+x" or (mode is None and any(fnmatch.fnmatchcase(f"/{rel}", p) for p in executable)):
        return tracked | 0o111
    return int(mode, 8) if mode else tracked


def _copy_args(line: str) -> tuple[str, str] | None:
    """(source, destination) of the line's copy, move or install."""
    found = _COPY_RE.search(line)
    verb = found.group(1) or found.group(2)
    words = _words(line, found.start(1) if found.group(1) else found.start(2))
    if words is None:
        return None
    args, takes_value = [], False
    for word in words[1:]:
        if takes_value:
            takes_value = False
        elif verb == "install" and word in ("-m", "-o", "-g", "-t", "--mode", "--owner", "--group"):
            takes_value = True
        elif not word.startswith("-"):
            args.append(word)
    return (args[0], args[1]) if len(args) == 2 else None


def _tree_copy_verdict(src: str, dest: str) -> str | None:
    """'ok' for a copy of a file the sync installs, to where the sync installs
    it; a reason for a copy out of those trees elsewhere; None when the source
    is in neither tree."""
    if src.startswith(f"{CHECKOUT}/rootfs/"):
        rel = src.removeprefix(f"{CHECKOUT}/rootfs/")
        if "*" in rel:
            expected_dir = "/" + rel.rsplit("/", 1)[0]
            ok = dest.rstrip("/") == expected_dir
        else:
            expected = "/" + rel
            ok = dest == expected or dest.rstrip("/") == expected.rsplit("/", 1)[0]
        return "ok" if ok else f"rootfs/{rel} is copied to {dest}, but an update installs it at /{rel.rstrip('*')}"
    if src.startswith(f"{CHECKOUT}/system/"):
        rel = src.removeprefix(f"{CHECKOUT}/system/")
        key = "*.service" if rel.endswith(".service") else rel
        expected = SYSTEM_DESTINATIONS.get(key)
        if expected is None:
            return f"system/{rel} is copied by the image, and an update never installs it"
        ok = dest == expected or (expected.endswith("/") and dest.rstrip("/") == expected.rstrip("/"))
        return "ok" if ok else f"system/{rel} is copied to {dest}, but an update installs it at {expected}"
    return None


def _mode_verdict(line: str) -> str | None:
    """'ok' for a chmod giving files the sync installs the mode the sync gives
    them; a reason when it gives another; None when it names no rootfs/ file."""
    found = _MODE_RE.search(line)
    words = _words(line, found.start(1))
    if found.group(1) != "chmod" or words is None:
        return None
    operands = [w for w in words[1:] if not w.startswith("-")]
    if len(operands) < 2:
        return None
    mode, targets = operands[0], operands[1:]
    files = [rel for target in targets for rel in ROOTFS_FILES if fnmatch.fnmatchcase(f"/{rel}", target)]
    if not files:
        return None
    wrong = []
    for rel in files:
        synced = _sync_mode(rel)
        same = bool(synced & 0o111) if mode == "+x" else (
            re.fullmatch(r"0?[0-7]{3}", mode) is not None and int(mode, 8) == synced
        )
        if not same:
            wrong.append(f"/{rel} {mode}, where an update installs it {synced:04o}")
        # A file the update installs and the image never copied is the other
        # direction's to report (test_every_rootfs_file_reaches_a_flashed_card).
    return "ok" if not wrong else "the image sets " + "; ".join(wrong)


# --------------------------------------------------------------------------- #
# What no update has to install again, and why.
# --------------------------------------------------------------------------- #

# Stage lines, matched by a regex on the logical line. Each must match a line,
# and every line that installs something not declared must match one.
IMAGE_ONLY_LINES = {
    # The programs dependencies.env pins
    r"^cp (?:go-librespot|navidrome|camilladsp) /usr/local/bin/$"
    r"|^chmod \+x /usr/local/bin/(?:go-librespot|navidrome|camilladsp)$":
        "a dependencies.env program; the Update Manager installs every later version",
    r"apt-get install -y \"\./snap(?:server|client)_":
        "the snapcast .debs dependencies.env pins; the Update Manager installs every later version",
    r"^make install$":
        "NQPTP and shairport-sync, which the Update Manager rebuilds on the unit, and BlueALSA, "
        "which has no update path: a bump lands with the next image (dependencies.env)",
    r"^scons .*install$":
        "roc-toolkit, which has no update path: a bump lands with the next image (dependencies.env)",
    r"^git clone --branch v\$\{(?:BLUEZ_ALSA|ROC_TOOLKIT)_VERSION\} --depth 1 ":
        "a source tree in /tmp, which the make or scons install after it installs",
    r"^apt-get (?:remove|purge|autoremove)\b|^apt-mark manual netplan\.io$":
        "a removal: an update never removes a package, since a rollback cannot put one back",

    # The checkout and what is built from it — the Milō update moves all of it
    r"git clone \$\(printf '%q' \"\$\{MILO_REPO\}\"\)":
        "the checkout itself, which the Milō update moves to each release",
    r"python3 -m venv /home/milo/milo(?:/milo-client)?/venv":
        "the checkout's venvs, which the Milō update installs the lock into",
    r"pip install --require-hashes -r /home/milo/milo/":
        "the checkout's locks, which the Milō update installs",
    r"cp \"\$\{FRONTEND_TARBALL\}\"|tar -xzf /tmp/frontend-dist\.tar\.gz -C /home/milo/milo/frontend":
        "the release's frontend artifact, which the Milō update installs from the release",
    r"^chmod 755 /home/milo(?:/milo(?:/frontend)?)?$|^chmod -R 755 /home/milo/milo/frontend/dist$"
    r"|^chown -R milo:milo /home/milo/milo/frontend/dist$":
        "nginx's way to the frontend, opened once on the directories above it; dist/ itself is "
        "replaced by every Milō update, unpacked as milo with the release archive's modes",
    r"> /var/lib/milo-client/app-(?:version|payload)$":
        "the satellite identity a push rewrites with every payload it installs",
    r"^chown -R milo:milo /var/lib/milo(?:/camilladsp)?$":
        "the data directory's owner, set where the image makes it: the backend writes there as "
        "milo, and the sync gives milo:milo to every file it installs under it",

    # The satellite's tree, on the unified image
    r"/home/milo/milo/milo-client/system/":
        "the satellite's units: milo-first-boot enables them at conversion, and every push to the "
        "satellite installs them (milo-client-deploy-update)",
    r"^cp /home/milo/milo/milo-client/rootfs/(?:usr/local/bin|etc/sudoers\.d)/\* "
    r"|^chmod 0440 \"/etc/sudoers\.d/\$name\"$":
        "the satellite's helpers and policies, which milo-first-boot installs again at conversion "
        "and every push to the satellite refreshes",

    # Set once for the image, owned elsewhere afterwards
    r"usermod -aG \S+ milo$":
        "the device groups of the account the image creates, set with the account",
    r"^plymouth-set-default-theme milo$|^update-initramfs -u$":
        "the splash chosen once; its theme files are rootfs/ files every update installs",
    r"^systemctl set-default graphical\.target$":
        "the boot target, chosen once; milo-kiosk is WantedBy it",
    r"^systemctl enable milo-kiosk\.service$":
        "a seed: milo-apply-hardware enables or disables the kiosk for the screen from the setup "
        "wizard on, and an update must not turn it back on over no screen",
    r"^systemctl disable (?:snapserver|snapclient|lightdm|NetworkManager-wait-online"
    r"|shairport-sync|bluealsa(?:-aplay)?|milo-client(?:-snapclient|-camilladsp)?)\.service\b"
    r"|^systemctl disable bluealsa-aplay\.service bluealsa\.service\b"
    r"|^systemctl disable snapserver\.service snapclient\.service\b":
        "a default an upstream install or the base image enables, turned off once: a package "
        "upgrade keeps an administrator's disable, and the masks are the manifest's",
    r"rm -f /etc/nginx/sites-enabled/default$":
        "Debian's default site, which the nginx package puts in place at its first install only",

    # Cosmetic: the console a kiosk never shows
    r"cp -r /usr/share/icons/Adwaita/cursors|cp /tmp/transparent_cursor":
        "the transparent cursor, written over a package's own icons once",
    r"cp /etc/issue /etc/issue\.backup|echo \"\" > /etc/issue$|rm -f /etc/issue\.d/IP\.issue$":
        "the login banner of a console the kiosk covers",
}

# provisioning/ functions the stage calls, each with the lines of its own body
# its reason covers. Every other installing line of the function — and every
# line of the functions it calls — is judged like a stage line, so a package or
# a unit added to one of them is caught as anywhere else.
IMAGE_ONLY_FUNCTIONS = {
    "configure_alsa_complete": (
        r'^tee "\$MILO_DATA_DIR/(?:routing|snapclient|mac)\.env" '
        r'|^chown "\$MILO_USER:\$MILO_USER" "\$MILO_DATA_DIR/routing\.env" ',
        "routing.env, snapclient.env and mac.env, seeds of the backend's runtime state, which it "
        "rewrites from its settings",
    ),
    "configure_go_librespot": (
        r'^tee "\$MILO_DATA_DIR/go-librespot/config\.yml" '
        r'|^chown -R "\$MILO_USER:audio" "\$MILO_DATA_DIR/go-librespot";?$',
        "a seed under /var/lib/milo: SpotifySource rewrites the keys Milō manages before every "
        "daemon start, and the rest are the daemon's defaults",
    ),
    "install_qobuz_proxy": (
        r'^python3 -m venv "\$MILO_DATA_DIR/qobuz/venv";?$|^"\$MILO_DATA_DIR/qobuz/venv/bin/pip" install '
        r'|^chown -R "\$MILO_USER:audio" "\$MILO_DATA_DIR/qobuz";?$|^chmod 0700 "\$MILO_DATA_DIR/qobuz";?$',
        "a dependencies.env program the Update Manager installs every later version of, into the "
        "venv made here; the directory is made once, milo:audio and 0700 (it holds the account's "
        "token), and no update makes it again",
    ),
    "configure_qobuz_proxy": (
        r'^tee "\$MILO_DATA_DIR/qobuz/config\.yaml" ',
        "the sidecar's config, a seed its web interface rewrites",
    ),
    "configure_navidrome": (
        r'^tee "\$MILO_DATA_DIR/navidrome/navidrome\.toml" '
        r'|^chown (?:-R )?"\$MILO_USER:\$MILO_USER" (?:"\$MILO_DATA_DIR/navidrome"|/media/milo);?$',
        "milo-navidrome-config.service runs this function again before every start",
    ),
    "configure_snapserver": (
        r"^tee /etc/snapserver\.conf ",
        "the backend rewrites its [http] and [tcp-control] sections and every source at each "
        "start (backend/core/multiroom/snapcast.py); the [stream] tuning is the image's",
    ),
    "configure_ir_overlay": (
        r'^sed -i .* "\$config_file";?$|^tee -a "\$config_file" ',
        "a seed: milo-apply-hardware owns the MILO IR block from the setup wizard on, and an "
        "update must not put back an IR the owner turned off",
    ),
    "configure_bluez_le": (
        r'^sed -i .* "\$conf";?$',
        "four LE parameters edited into BlueZ's own main.conf, which takes no drop-in",
    ),
}

# Functions with nothing to install: the log helpers and the boot path lookup.
_NEUTRAL_FUNCTIONS = {"log_info", "log_success", "log_warning", "boot_config_file"}


def _stage_scripts() -> list[Path]:
    scripts = sorted(STAGE_DIR.rglob("*.sh"))
    assert len(scripts) >= 6, f"only {scripts} stage scripts found"
    return scripts


def _called_functions(text: str, bodies: dict[str, str]) -> list[str]:
    """The provisioning/ functions a text calls, and every one they call in turn."""
    pending = [n for n in CALL_RE.findall(text) if n in bodies]
    seen: list[str] = []
    while pending:
        name = pending.pop(0)
        if name in seen:
            continue
        seen.append(name)
        pending.extend(n for n in CALL_RE.findall(bodies[name]) if n in bodies)
    return seen


@dataclass(frozen=True)
class Verdict:
    unaccounted: list[str]
    misplaced: list[str]
    unused_lines: list[str]
    unused_functions: list[str]
    tree_copies: list[tuple[str, str]]


def _classify(stage: dict[str, str], bodies: dict[str, str]) -> Verdict:
    """Every installing line of `stage` ({name: text}) and of the functions it
    calls, against the manifest, the two trees and the two lists above."""
    unaccounted, misplaced, tree_copies = [], [], []
    used_lines, used_functions = set(), set()

    def judge(site: Site, function: str | None = None) -> None:
        if site.kind == "copy":
            args = _copy_args(site.line)
            verdict = _tree_copy_verdict(*args) if args else None
            if verdict == "ok":
                tree_copies.append(args)
                return
            if verdict is not None and not any(re.search(rx, site.line) for rx in IMAGE_ONLY_LINES):
                misplaced.append(f"{site.where}: {verdict}")
                return
        if site.kind == "mode":
            verdict = _mode_verdict(site.line)
            if verdict == "ok":
                return
            if verdict is not None:
                misplaced.append(f"{site.where}: {verdict}")
                return
        if site.kind == "apt" and re.search(_MANIFEST_PACKAGES, site.line):
            return
        if site.kind == "other" and re.fullmatch(_APPLY_MANIFEST, site.line):
            return
        if function in IMAGE_ONLY_FUNCTIONS and re.search(IMAGE_ONLY_FUNCTIONS[function][0], site.line):
            used_functions.add(function)
            return
        matches = [rx for rx in IMAGE_ONLY_LINES if re.search(rx, site.line)]
        if matches:
            used_lines.update(matches)
            return
        unaccounted.append(f"{site.where}: [{site.kind}] {site.line}")

    called: list[str] = []
    for name, text in stage.items():
        for site in _sites(name, text):
            judge(site)
        called += [f for f in _called_functions(text, bodies) if f not in called]

    for function in called:
        if function in _NEUTRAL_FUNCTIONS:
            continue
        for site in _sites(f"{function}()", bodies[function]):
            judge(site, function)

    return Verdict(
        unaccounted=unaccounted,
        misplaced=misplaced,
        unused_lines=sorted(set(IMAGE_ONLY_LINES) - used_lines),
        unused_functions=sorted(set(IMAGE_ONLY_FUNCTIONS) - used_functions),
        tree_copies=tree_copies,
    )


@pytest.fixture(scope="module")
def bodies() -> dict[str, str]:
    parsed = _function_bodies()
    assert "configure_ir_overlay" in parsed and "install_ir_helpers" in parsed, sorted(parsed)
    return parsed


@pytest.fixture(scope="module")
def stage() -> dict[str, str]:
    return {str(p.relative_to(REPO_ROOT)): p.read_text() for p in _stage_scripts()}


def test_the_stage_extractor_reads_every_kind_of_line(stage, bodies):
    """A line kind the patterns stopped seeing reads as "nothing to install"."""
    kinds = {site.kind for name, text in stage.items() for site in _sites(name, text)}
    assert kinds == {"copy", "apt", "unit", "write", "mode", "other"}, kinds
    copies = _classify(stage, bodies).tree_copies
    assert len(copies) >= 25, f"only {len(copies)} rootfs/system copies read: {copies}"
    assert (f"{CHECKOUT}/rootfs/etc/nginx/sites-enabled/milo", "/etc/nginx/sites-enabled/milo") in copies
    assert (f"{CHECKOUT}/rootfs/usr/local/bin/*", "/usr/local/bin/") in copies


def test_everything_the_image_installs_is_declared_or_justified(stage, bodies):
    """Whatever pi-gen/stage-milo puts on a card, an update puts on a unit too —
    or the reason it need not is written here.

    Fails on a line that installs a package other than the manifest's, enables
    or masks a unit, writes a file of the target, sets a mode or an owner on
    it, runs a helper of the tree, or copies a file from outside rootfs/ and
    system/, with no entry above — or that gives a rootfs/ file another mode
    than the sync gives it: a release adding one reaches every
    card flashed after it and no unit already running, as libcairo2 did. Move
    the file into rootfs/, the package, boot line or unit into
    system-manifest.env, or — when an update truly has nothing to redo — add an
    entry with its reason. Consumers: every Milō update
    (`milo-deploy-update sync-system-files`), and the update guard's sync back.
    """
    verdict = _classify(stage, bodies)
    assert not verdict.unaccounted, (
        "the image installs these and no update does:\n" + "\n".join(verdict.unaccounted)
    )
    assert not verdict.misplaced, "\n".join(verdict.misplaced)


def test_every_reason_still_has_a_line(stage, bodies):
    """A justification nothing matches is a list that can no longer fail."""
    verdict = _classify(stage, bodies)
    assert not verdict.unused_lines, f"IMAGE_ONLY_LINES entries matching no line: {verdict.unused_lines}"
    assert not verdict.unused_functions, (
        f"IMAGE_ONLY_FUNCTIONS entries the stage no longer calls, or whose lines are gone: "
        f"{verdict.unused_functions}"
    )


def _chroot(*lines: str) -> str:
    return "on_chroot << 'CHROOT'\n" + "\n".join(lines) + "\nCHROOT\n"


@pytest.mark.parametrize("drift", [
    'on_chroot << CHROOT\napt-get install -y libfoo1\nCHROOT\n',
    # The wrapper as upstream pi-gen's stages spell it, and its other spellings
    'on_chroot << EOF\napt-get install -y libfoo1\nsystemctl enable foo.service\nEOF\n',
    'on_chroot << "CHROOT"\napt-get install -y libfoo1\nCHROOT\n',
    'on_chroot <<- CHROOT\n\tapt-get install -y libfoo1\n\tCHROOT\n',
    # A shell fed from a heredoc inside the wrapper
    _chroot("sudo -u milo bash << 'SH'", "apt-get install -y libfoo1", "SH"),
    # A heredoc that is no file's content: the body is the command's input
    _chroot("cat << 'EOF' | bash", "systemctl enable foo.service", "EOF"),
    _chroot("systemctl enable milo-foo.service"),
    _chroot("systemctl mask foo.service"),
    _chroot('echo "options snd-foo index=1" > /etc/modprobe.d/snd-foo.conf'),
    _chroot("tee /etc/nginx/conf.d/foo.conf > /dev/null << 'EOF'", "x", "EOF"),
    _chroot("echo dtparam=foo=on >> /boot/firmware/config.txt"),
    _chroot("sed -i 's/a/b/' /etc/default/foo"),
    _chroot("cp /tmp/foo.conf /etc/foo.conf"),
    _chroot("cp /home/milo/milo/rootfs/etc/foo.conf /etc/bar.conf"),
    _chroot("cp /home/milo/milo/system/foo.timer /etc/systemd/system/"),
    _chroot("ln -sf /etc/foo /etc/bar"),
    _chroot("cd /home/milo/milo", "source provisioning/common.sh", "configure_foo"),
    _chroot("install -m 0644 /home/milo/milo/foo.conf /etc/foo.conf"),
    # pi-gen's own idiom, run on the host into the target
    'install -m 644 files/foo.conf "${ROOTFS_DIR}/etc/foo.conf"\n',
    _chroot("mv /tmp/foo.conf /etc/foo.conf"),
    _chroot("rsync -a /tmp/foo/ /etc/foo/"),
    _chroot("wget -q -O /usr/local/bin/foo https://example.invalid/foo"),
    _chroot("curl -fsSL -o /etc/foo.conf https://example.invalid/foo.conf"),
    _chroot("unzip -q /tmp/foo.zip -d /opt/foo"),
    _chroot("chmod 0700 /var/lib/milo/foo"),
    _chroot("chmod 0755 /etc/udev/rules.d/90-milo-cd.rules"),
    _chroot('echo "foo foo/bar boolean true" | debconf-set-selections'),
    _chroot("dpkg-reconfigure -f noninteractive foo"),
    _chroot("raspi-config nonint do_foo 1"),
    _chroot("/usr/local/bin/milo-apply-hardware"),
    # A package added inside a function whose other lines are justified
    _chroot("cd /home/milo/milo", "source provisioning/common.sh", "install_qobuz_proxy"),
], ids=["package", "chroot-eof", "chroot-double-quoted", "chroot-dash", "shell-heredoc",
        "piped-heredoc", "enable", "mask", "inline-file", "tee", "config-txt", "sed", "foreign-copy",
        "rootfs-elsewhere", "unsynced-system-file", "symlink", "unjustified-function", "install",
        "host-install", "mv", "rsync", "wget", "curl", "unzip", "chmod", "chmod-of-a-tree-file",
        "debconf", "dpkg-reconfigure", "raspi-config", "helper", "exempted-function"])
def test_the_guardrail_goes_red_on_a_simulated_drift(drift, bodies):
    """Each way a stage line can put something no update reproduces is caught."""
    fake = dict(
        bodies,
        configure_foo='    echo "x" > /etc/foo.conf\n',
        install_qobuz_proxy=bodies["install_qobuz_proxy"] + "\n    apt-get install -y libportaudio2;\n",
    )
    verdict = _classify({"pi-gen/stage-milo/99-drift/00-run.sh": drift}, fake)
    assert verdict.unaccounted or verdict.misplaced, f"not caught:\n{drift}"


def test_only_a_file_written_from_a_heredoc_goes_unread(bodies):
    """The one body not read as code is the one `tee` or `cat` writes to the
    target: it is judged through its opener, a write site, and the lines after
    its delimiter are code again. A heredoc never closed fails the parse, since
    skipping the rest of a file would read as nothing to install."""
    written = _chroot(
        "tee /etc/foo.sh > /dev/null << 'EOF'", "apt-get install -y libfoo1", "EOF",
        "systemctl enable foo.service",
    )
    unaccounted = _classify({"pi-gen/stage-milo/99-drift/00-run.sh": written}, bodies).unaccounted
    assert [line.split("] ", 1)[1] for line in unaccounted] == [
        "tee /etc/foo.sh > /dev/null << 'EOF'", "systemctl enable foo.service",
    ], unaccounted
    with pytest.raises(AssertionError, match="never closed"):
        _logical_lines("on_chroot << EOF\napt-get install -y libfoo1\n")


def test_a_copy_an_update_installs_too_needs_no_reason(bodies):
    """The guardrail is not red on everything: a rootfs/ or system/ file copied
    where the sync installs it, with the mode the sync gives it, and the
    manifest's packages, pass."""
    clean = (
        "source \"$(dirname \"${BASH_SOURCE[0]}\")/../system-manifest.env\"\n"
        "on_chroot << CHROOT\napt-get -o Acquire::Retries=3 install -y ${SYSTEM_APT_PACKAGES[*]}\nCHROOT\n"
        "on_chroot << 'CHROOT'\n"
        "cp /home/milo/milo/rootfs/etc/modprobe.d/snd-aloop.conf /etc/modprobe.d/snd-aloop.conf\n"
        "cp /home/milo/milo/rootfs/etc/udev/rules.d/90-milo-cd.rules /etc/udev/rules.d/\n"
        "chmod 0644 /etc/udev/rules.d/90-milo-cd.rules\n"
        "for f in /home/milo/milo/system/*.service; do\n    cp \"$f\" /etc/systemd/system/\ndone\n"
        "CHROOT\n"
    )
    verdict = _classify({"pi-gen/stage-milo/99-clean/00-run.sh": clean}, bodies)
    assert not verdict.unaccounted and not verdict.misplaced, verdict
    assert len(verdict.tree_copies) == 3, verdict.tree_copies


def test_every_rootfs_file_reaches_a_flashed_card(stage, bodies):
    """The other direction: what an update installs, the image installs too.

    Fails when a file is added to rootfs/ and no stage line copies it (by name,
    or by a glob over its directory): every unit already running gets it at its
    next update, and every card flashed from the image goes without it until
    then — the one unit just set up behaving unlike the rest. Consumers: the
    image build and `pi-gen/inspect-image.sh`.
    """
    copied = [
        src.removeprefix(f"{CHECKOUT}/rootfs/")
        for src, _ in _classify(stage, bodies).tree_copies
        if src.startswith(f"{CHECKOUT}/rootfs/")
    ]
    files = sorted(str(p.relative_to(ROOTFS)) for p in ROOTFS.rglob("*") if p.is_file())
    assert len(files) >= 40 and copied, f"rootfs files or stage copies not read: {len(files)}, {copied}"
    unflashed = [f for f in files if not any(fnmatch.fnmatch(f, glob) for glob in copied)]
    assert not unflashed, f"pi-gen/stage-milo never installs these rootfs files: {unflashed}"


def _unread_stage_files(stage_dir: Path) -> list[str]:
    """Files of a stage this guardrail does not read: anything but a script and
    the marker that makes pi-gen export the image."""
    return sorted(
        str(p.relative_to(stage_dir)) for p in stage_dir.rglob("*")
        if p.is_file() and p.suffix != ".sh" and p.name != "EXPORT_IMAGE"
    )


def test_the_stage_holds_nothing_the_guardrail_does_not_read(tmp_path):
    """pi-gen acts on more than a substage's scripts — it installs its
    `NN-packages` list, preseeds its `NN-debconf`, applies its `NN-patches` —
    and none of them is read here: a package list there is a second
    declaration no update reads, the very file libcairo2 was added to, and a
    `files/` tree is a copy the stage would install from outside rootfs/."""
    for name in ("00-install-deps/00-packages", "00-install-deps/01-debconf", "03-configure/files/foo.conf"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("x\n")
    (tmp_path / "03-configure" / "00-run.sh").write_text("#!/bin/bash -e\n")
    (tmp_path / "EXPORT_IMAGE").write_text("")
    assert _unread_stage_files(tmp_path) == [
        "00-install-deps/00-packages", "00-install-deps/01-debconf", "03-configure/files/foo.conf",
    ], "the check no longer sees a stage file it cannot read"

    assert (STAGE_DIR / "EXPORT_IMAGE").is_file(), "the stage was not found"
    assert not _unread_stage_files(STAGE_DIR), (
        f"stage files this guardrail does not read: {_unread_stage_files(STAGE_DIR)}"
    )


# --------------------------------------------------------------------------- #
# One declaration, one implementation, on both paths.
# --------------------------------------------------------------------------- #

def _calls(body: str) -> list[str]:
    return [m for m in re.findall(r"^\s+([a-z_]+)\s*$", body, re.MULTILINE)]


def test_the_image_and_the_update_apply_the_manifest_with_the_same_code(stage):
    """The stage installs the manifest's packages and hands the rest to the
    deploy script; the sync applies the same functions after the release's files.

    Fails when either path stops reading `system-manifest.env`, or the two run
    different code for the boot lines and the unit states — the two spellings
    that drift. Consumers: the image build, every Milō update.
    """
    stage_text = "\n".join(stage.values())
    assert re.search(r'^source "\$\(dirname "\$\{BASH_SOURCE\[0\]\}"\)/\.\./system-manifest\.env"$',
                     stage_text, re.MULTILINE), "the stage no longer sources the manifest beside it"
    assert re.search(r"^/usr/local/bin/milo-deploy-update apply-system-manifest$", stage_text, re.MULTILINE), (
        "the stage no longer applies the manifest through the deploy script"
    )

    functions = _deploy_functions()
    image_half = _calls(functions["cmd_apply_system_manifest"])
    update = _calls(functions["cmd_sync_system_files"])
    assert image_half[0] == "read_system_manifest" and len(image_half) >= 4, image_half
    assert "read_system_manifest" in update, f"the sync no longer reads the manifest: {update}"
    shared = image_half[1:]
    applied = update[update.index("read_system_manifest") + 1:]
    assert [c for c in applied if c in shared] == shared, (
        f"the sync applies {update}, the image {image_half}: one is missing a step of the other"
    )
    assert "install_system_packages" in applied


def test_the_sync_applies_the_manifest_after_the_files():
    """On the guard's sync back, a manifest the newer script cannot read — a
    release older than the manifest — or a package that cannot be installed
    must not leave the newer release's files over the older release's code: the
    files go back first, then the manifest is read. And the unit states come
    after the packages, which may bring a unit."""
    body = _deploy_functions()["cmd_sync_system_files"]
    marks = [body.find(m) for m in (
        'echo "Deploying rootfs files..."', "systemctl daemon-reload", "    read_system_manifest",
        "    install_system_packages", "    apply_unit_states",
    )]
    assert -1 not in marks, f"sync-system-files not read: {marks}"
    assert marks == sorted(marks), marks


def _guard_constant(name: str) -> int:
    found = re.search(rf"^{name}\s*=\s*(\d+)", GUARD.read_text(), re.MULTILINE)
    assert found, f"{name} not found in milo-update-guard"
    return int(found.group(1))


def test_a_rollback_can_wait_for_dpkg_and_still_finish():
    """The guard gives the whole sync back DEPLOY_TIMEOUT_S, at boot, inside the
    backend's TimeoutStartSec. A lock wait as long would time the rollback out
    on a unit whose dpkg was merely busy."""
    lock = int(re.search(r"^DPKG_LOCK_TIMEOUT_S=(\d+)$", DEPLOY.read_text(), re.MULTILINE).group(1))
    assert 0 < lock <= _guard_constant("DEPLOY_TIMEOUT_S") // 2


# --------------------------------------------------------------------------- #
# The application itself, run on files.
# --------------------------------------------------------------------------- #

def _harness(tmp_path: Path, manifest: Path = MANIFEST) -> Path:
    """The deploy script's functions, with the boot partition and the lock in
    `tmp_path` — everything but the dispatcher, as the script defines it."""
    text = DEPLOY.read_text()
    functions = text[:text.index("# --- Main dispatcher ---")]
    script = tmp_path / "harness.sh"
    script.write_text(
        functions
        + f'\nMANIFEST="{manifest}"\nCMDLINE_LOCK="{tmp_path}/lock"\n'
        + f'boot_file() {{ echo "{tmp_path}/$1"; }}\n'
    )
    return script


def _run(tmp_path: Path, steps: str, *, manifest: Path = MANIFEST, path: str | None = None):
    env = dict(os.environ)
    if path:
        env["PATH"] = f"{path}:{env['PATH']}"
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{_harness(tmp_path, manifest)}"; read_system_manifest; {steps}'],
        capture_output=True, text=True, timeout=60, env=env, check=False,
    )


STOCK_CMDLINE = "console=serial0,115200 console=tty1 root=PARTUUID=4e639091-02 rootfstype=ext4 fsck.repair=yes rootwait\n"
# What Raspberry Pi OS ships, then the IR seed the stage writes before the
# manifest, then the blocks the setup wizard appends after it.
STOCK_CONFIG = "dtparam=audio=on\n[all]\ndtoverlay=vc4-kms-v3d\n\n# BEGIN MILO IR\ndtoverlay=gpio-ir,gpio_pin=17\n# END MILO IR\n"


def test_the_boot_lines_are_applied_once_whatever_runs_them(tmp_path, manifest):
    """The image's apply and every update's give one config.txt and one
    cmdline.txt: each later run changes nothing.

    Fails when a run appends a second block or a second token, or drops a line
    it does not own — root= above all, without which the unit does not boot.
    """
    (tmp_path / "cmdline.txt").write_text(STOCK_CMDLINE)
    (tmp_path / "config.txt").write_text(STOCK_CONFIG)

    states = []
    for _ in range(3):
        result = _run(tmp_path, "apply_config_txt; apply_cmdline")
        assert result.returncode == 0, result.stderr
        states.append(((tmp_path / "config.txt").read_text(), (tmp_path / "cmdline.txt").read_text()))
    assert states[1] == states[0] and states[2] == states[0]

    config, cmdline = states[0]
    assert config.startswith(STOCK_CONFIG), "a line the manifest does not own moved"
    block = config.split("# BEGIN MILO SYSTEM\n", 1)[1].split("\n# END MILO SYSTEM", 1)[0]
    assert block.splitlines() == manifest["SYSTEM_CONFIG_TXT"]

    tokens = cmdline.split()
    assert tokens[:4] == ["root=PARTUUID=4e639091-02", "rootfstype=ext4", "fsck.repair=yes", "rootwait"]
    assert tokens[-len(manifest["SYSTEM_CMDLINE"]):] == manifest["SYSTEM_CMDLINE"]
    assert "console=serial0,115200" not in tokens and "console=tty1" not in tokens
    for seed in manifest["SYSTEM_CMDLINE_SEED"]:
        assert tokens.count(seed) == 1


def test_an_update_replaces_what_it_owns_and_keeps_what_it_does_not(tmp_path, manifest):
    """A release that moves a managed value reaches the unit; the radio's
    country, set by milo-set-wifi-country, and the blocks milo-apply-hardware
    appended after the manifest's, stay as they are.

    Fails on the regression this replaces: the image's cmdline writer stripped
    every cfg80211 token and appended `=00` again, so run by an update it
    would have reset every unit's WiFi country to the world domain.
    """
    # An earlier release's manifest: one managed token and one config.txt line
    # held another value.
    token = next(t for t in manifest["SYSTEM_CMDLINE"] if re.fullmatch(r"[\w.]+=\d+", t))
    line = next(t for t in manifest["SYSTEM_CONFIG_TXT"] if re.fullmatch(r"dtparam=\w+=\d+", t))
    old_token = re.sub(r"\d+$", lambda m: str(int(m.group()) + 1), token)
    old_line = re.sub(r"\d+$", lambda m: str(int(m.group()) + 1), line)
    old = tmp_path / "old.env"
    old.write_text(
        re.sub(rf"(?<=\s){re.escape(token)}(?=\s)", old_token, MANIFEST.read_text())
        .replace(f'"{line}"', f'"{old_line}"')
    )
    earlier = _manifest(old)
    assert old_token in earlier["SYSTEM_CMDLINE"] and old_line in earlier["SYSTEM_CONFIG_TXT"], earlier
    (tmp_path / "cmdline.txt").write_text(STOCK_CMDLINE)
    (tmp_path / "config.txt").write_text(STOCK_CONFIG)
    assert _run(tmp_path, "apply_config_txt; apply_cmdline", manifest=old).returncode == 0

    # What the unit then does on its own: a country, and the wizard's blocks.
    cmdline = (tmp_path / "cmdline.txt").read_text().replace("regdom=00", "regdom=FR")
    (tmp_path / "cmdline.txt").write_text(cmdline)
    wizard = "\n# BEGIN MILO AUDIO\ndtoverlay=hifiberry-amp4pro\n# END MILO AUDIO\n"
    (tmp_path / "config.txt").write_text((tmp_path / "config.txt").read_text() + wizard)

    result = _run(tmp_path, "apply_config_txt; apply_cmdline")
    assert result.returncode == 0, result.stderr

    tokens = (tmp_path / "cmdline.txt").read_text().split()
    assert token in tokens and old_token not in tokens
    assert [t for t in tokens if t.startswith("cfg80211.ieee80211_regdom=")] == ["cfg80211.ieee80211_regdom=FR"]
    config = (tmp_path / "config.txt").read_text()
    assert line in config.splitlines() and old_line not in config.splitlines()
    assert config.count("# BEGIN MILO SYSTEM") == 1
    assert config.endswith(wizard), "the wizard's blocks did not keep their place after the manifest's"


@pytest.mark.parametrize("damage", [
    lambda config: config.replace("# END MILO SYSTEM\n", ""),
    lambda config: config + "\n# BEGIN MILO SYSTEM\n[all]\n# END MILO SYSTEM\n",
], ids=["unterminated", "doubled"])
def test_a_block_the_apply_cannot_place_is_left_alone(tmp_path, damage):
    """A block with no end would swallow the rest of config.txt — the audio
    card's overlay included — so the apply refuses, and says where."""
    (tmp_path / "cmdline.txt").write_text(STOCK_CMDLINE)
    (tmp_path / "config.txt").write_text(STOCK_CONFIG)
    assert _run(tmp_path, "apply_config_txt").returncode == 0
    broken = damage((tmp_path / "config.txt").read_text())
    (tmp_path / "config.txt").write_text(broken)

    result = _run(tmp_path, "apply_config_txt")

    assert result.returncode != 0
    assert "MILO SYSTEM" in result.stderr.strip().splitlines()[-1]
    assert (tmp_path / "config.txt").read_text() == broken


def test_a_cmdline_without_root_is_never_written(tmp_path):
    (tmp_path / "cmdline.txt").write_text("quiet splash\n")
    result = _run(tmp_path, "apply_cmdline")
    assert result.returncode != 0 and "root=" in result.stderr
    assert (tmp_path / "cmdline.txt").read_text() == "quiet splash\n"


def test_the_boot_files_are_written_through_a_renamed_sibling():
    """/boot/firmware is vfat, with no journal: the one function that writes a
    boot file renames a flushed sibling over it, then flushes the file, and the
    two appliers write through it alone."""
    functions = _deploy_functions()
    body = functions["write_boot_file"].splitlines()
    assert '    mv -f "$tmp" "$target"' in body, body
    after = body[body.index('    mv -f "$tmp" "$target"') + 1:]
    assert '    sync -f "$target"' in after and '    sync "$target"' in after, body
    for applier in ("apply_config_txt", "apply_cmdline"):
        code = functions[applier]
        assert "write_boot_file" in code, applier
        assert not re.search(r"(?<![<>&0-9])>\s*\"?\$(?:file|current)\b", code), f"{applier} writes in place"


# --------------------------------------------------------------------------- #
# Packages: the world is apt and dpkg, stubbed.
# --------------------------------------------------------------------------- #

def _stub_world(
    tmp_path: Path, *, installed: set[str], apt_fails: str = "", update_says: str = "",
    dpkg_locked_for: int = 0,
) -> Path:
    """`dpkg-query`, `dpkg` and `apt-get` as the sync meets them: a status per
    package, a dpkg lock held for N tries (in dpkg 1.22's words, measured on
    trixie), an `update` that prints what it is
    given and exits 0 (as apt does over a source it cannot reach), an install
    that installs or fails. Every call is logged to calls.log."""
    (tmp_path / "update-says").write_text(update_says)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "installed").write_text("\n".join(sorted(installed)) + "\n")
    (tmp_path / "locked").write_text(str(dpkg_locked_for))
    log = tmp_path / "calls.log"
    scripts = {
        "dpkg-query": f"""#!/bin/bash
pkg="${{@: -1}}"
grep -qx "$pkg" "{tmp_path}/installed" && printf installed && exit 0
echo "dpkg-query: no packages found matching $pkg" >&2; exit 1
""",
        "dpkg": f"""#!/bin/bash
echo "dpkg $*" >> "{log}"
n=$(cat "{tmp_path}/locked")
if [ "$n" -gt 0 ]; then echo $((n - 1)) > "{tmp_path}/locked"
  echo "dpkg: error: dpkg frontend lock was locked by /usr/bin/python3.13 process with pid 4242" >&2
  echo "Note: removing the lock file is always wrong, can damage the locked area" >&2
  echo "and the entire system. See <https://wiki.debian.org/Teams/Dpkg/FAQ#db-lock>." >&2; exit 2; fi
""",
        "apt-get": f"""#!/bin/bash
echo "apt-get $*" >> "{log}"
[[ " $* " == *" update "* ]] && cat "{tmp_path}/update-says" && exit 0
if [ -n "{apt_fails}" ]; then echo "Reading package lists..."; echo "E: {apt_fails}"; exit 100; fi
for a in "$@"; do case "$a" in -*|*=*|install) ;; *) echo "$a" >> "{tmp_path}/installed" ;; esac; done
""",
        "sleep": "#!/bin/bash\nexit 0\n",
    }
    for name, body in scripts.items():
        path = bin_dir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    log.write_text("")
    return bin_dir


def _calls_made(tmp_path: Path) -> list[str]:
    return (tmp_path / "calls.log").read_text().splitlines()


def test_a_unit_with_every_package_reaches_no_network(tmp_path, manifest):
    """The guard's sync back to a release whose packages were all there runs at
    boot, possibly offline: it must not even read the package lists."""
    world = _stub_world(tmp_path, installed=set(manifest["SYSTEM_APT_PACKAGES"]))

    result = _run(tmp_path, "install_system_packages", path=str(world))

    assert result.returncode == 0, result.stderr
    assert [c for c in _calls_made(tmp_path) if c.startswith("apt-get")] == []
    assert _calls_made(tmp_path) == ["dpkg --configure -a --force-confdef --force-confold"]


def test_a_missing_package_is_installed_alone_and_without_removals(tmp_path, manifest):
    """libcairo2's case: a release names one more package. Exactly that one is
    installed, after the lists are read, without recommends, refusing any
    removal; then it is checked installed."""
    packages = manifest["SYSTEM_APT_PACKAGES"]
    world = _stub_world(tmp_path, installed=set(packages) - {"libcairo2"})

    result = _run(tmp_path, "install_system_packages", path=str(world))

    assert result.returncode == 0, result.stderr
    apt = [c.split() for c in _calls_made(tmp_path) if c.startswith("apt-get")]
    assert [c for c in apt if "update" in c] and apt[0][-1] == "update", apt
    install = apt[1]
    assert install[-1] == "libcairo2", install
    assert [w for w in install if w in packages] == ["libcairo2"], install
    assert {"--no-install-recommends", "--no-remove", "-y"} <= set(install)
    assert any(w.startswith("DPkg::Lock::Timeout=") for w in install), install


def test_a_package_apt_cannot_install_fails_the_sync_with_a_line_that_says_why(tmp_path, manifest):
    """Before, the trial failed its starts and rolled back with no cause on
    screen. Now the sync fails — before the reboot, so the updater hands over —
    and its last line names the package and apt's own reason, which is the line
    the guard and the update screen read."""
    world = _stub_world(
        tmp_path, installed=set(manifest["SYSTEM_APT_PACKAGES"]) - {"libcairo2"},
        apt_fails="Unable to locate package libcairo2",
    )

    result = _run(tmp_path, "install_system_packages", path=str(world))

    assert result.returncode != 0
    last = result.stderr.strip().splitlines()[-1]
    assert "libcairo2" in last and "E: Unable to locate package libcairo2" in last, last
    assert "could not reach" not in last, last


def test_a_source_apt_could_not_reach_is_named_beside_the_package_it_could_not_find(tmp_path, manifest):
    """apt-get update exits 0 over a mirror it cannot reach, and the image wipes
    the lists, so the install that follows reports the package as unknown. The
    last line must name the unreached source too, or the owner checks the
    package's name while the mirror is down (github.com reachable, so the
    release downloaded, and deb.debian.org not)."""
    fetch = ("W: Failed to fetch http://deb.debian.org/debian/dists/trixie/InRelease  "
             "Temporary failure resolving 'deb.debian.org'")
    world = _stub_world(
        tmp_path, installed=set(manifest["SYSTEM_APT_PACKAGES"]) - {"libcairo2"},
        update_says=(
            "Err:1 http://deb.debian.org/debian trixie InRelease\n"
            "  Temporary failure resolving 'deb.debian.org'\nReading package lists...\n"
            f"{fetch}\nW: Some index files failed to download. They have been ignored, or old ones used instead.\n"
        ),
        apt_fails="Unable to locate package libcairo2",
    )

    result = _run(tmp_path, "install_system_packages", path=str(world))

    assert result.returncode != 0
    last = result.stderr.strip().splitlines()[-1]
    assert "E: Unable to locate package libcairo2" in last, last
    assert fetch.removeprefix("W: ") in last, last


def test_an_interrupted_dpkg_is_finished_once_its_lock_is_free(tmp_path, manifest):
    """dpkg does not wait for its lock where apt-get does: the run that finishes
    a cut install is retried while another dpkg holds it."""
    world = _stub_world(tmp_path, installed=set(manifest["SYSTEM_APT_PACKAGES"]), dpkg_locked_for=3)

    result = _run(tmp_path, "install_system_packages", path=str(world))

    assert result.returncode == 0, result.stderr
    assert _calls_made(tmp_path).count("dpkg --configure -a --force-confdef --force-confold") == 4
    assert "WARNING" not in result.stdout


def test_a_dpkg_failure_that_is_not_its_lock_is_reported_at_once(tmp_path, manifest):
    """Only dpkg's own words for a held lock are waited out: any other failure
    — one naming libblockdev included — is a warning on the first try, not
    half of the guard's budget spent retrying it."""
    world = _stub_world(tmp_path, installed=set(manifest["SYSTEM_APT_PACKAGES"]))
    (world / "dpkg").write_text(
        f'#!/bin/bash\necho "dpkg $*" >> "{tmp_path}/calls.log"\n'
        "echo \"dpkg: error processing package libblockdev-utils3 (--configure):\" >&2; exit 1\n"
    )

    result = _run(tmp_path, "install_system_packages", path=str(world))

    assert result.returncode == 0, result.stderr
    assert _calls_made(tmp_path) == ["dpkg --configure -a --force-confdef --force-confold"]
    assert "WARNING: dpkg --configure -a failed: dpkg: error processing package libblockdev" in result.stdout


def test_units_are_enabled_and_masked_never_disabled(tmp_path, manifest):
    """Additive like the rest: a unit a later manifest drops stays as it was."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    (bin_dir / "systemctl").write_text(f'#!/bin/bash\necho "systemctl $*" >> "{log}"\n')
    (bin_dir / "systemctl").chmod(0o755)

    result = _run(tmp_path, "apply_unit_states", path=str(bin_dir))

    assert result.returncode == 0, result.stderr
    calls = [c.split() for c in log.read_text().splitlines()]
    assert [c[1] for c in calls] == ["enable", "mask"], calls
    assert calls[0][-len(manifest["SYSTEM_UNITS_ENABLED"]):] == manifest["SYSTEM_UNITS_ENABLED"]
    assert calls[1][-len(manifest["SYSTEM_UNITS_MASKED"]):] == manifest["SYSTEM_UNITS_MASKED"]


def test_a_tree_without_its_manifest_is_refused(tmp_path):
    """The guard's sync back to a release older than the manifest ends here,
    once its files are back, on a line naming the file."""
    result = _run(tmp_path, "true", manifest=tmp_path / "absent.env")
    assert result.returncode != 0
    assert "absent.env is missing" in result.stderr.strip().splitlines()[-1]


def test_the_image_subcommand_writes_the_boot_lines_and_the_units(tmp_path, manifest):
    """`apply-system-manifest`, as the stage runs it in its chroot: config.txt,
    cmdline.txt and the unit states, in one run, from the manifest alone — and
    a call with arguments is refused before it touches anything."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    (bin_dir / "systemctl").write_text(f'#!/bin/bash\necho "systemctl $*" >> "{log}"\n')
    (bin_dir / "systemctl").chmod(0o755)
    (tmp_path / "cmdline.txt").write_text(STOCK_CMDLINE)
    (tmp_path / "config.txt").write_text(STOCK_CONFIG)

    refused = _run(tmp_path, "cmd_apply_system_manifest --force", path=str(bin_dir))
    assert refused.returncode != 0 and not log.exists()
    assert (tmp_path / "config.txt").read_text() == STOCK_CONFIG

    result = _run(tmp_path, "cmd_apply_system_manifest", path=str(bin_dir))

    assert result.returncode == 0, result.stderr
    assert "# BEGIN MILO SYSTEM" in (tmp_path / "config.txt").read_text()
    assert "cgroup_enable=memory" in (tmp_path / "cmdline.txt").read_text().split()
    assert [c.split()[1] for c in log.read_text().splitlines()] == ["enable", "mask"]


# The manifest's array names cross releases, as the update journal's names and
# the satellite's `POST /app/update` fields do: the installed milo-deploy-update
# reads the tree it is handed — the arriving release's, and on the guard's sync
# back the previous release's, with the newer script. A name renamed in both in
# one commit keeps every same-HEAD check green and fails one of the two at every
# press: the departing script refuses the arriving manifest, or the rollback
# refuses the departing one. Append-only: an array added later goes in
# MANIFEST_ARRAYS_ADDED, which an older tree reads as empty.
FROZEN_MANIFEST_ARRAYS = (
    "SYSTEM_APT_PACKAGES", "SYSTEM_CMDLINE", "SYSTEM_CMDLINE_SEED", "SYSTEM_CONFIG_TXT",
    "SYSTEM_UNITS_ENABLED", "SYSTEM_UNITS_MASKED",
)


def test_the_manifest_array_names_are_frozen():
    """Fails when an array is renamed, removed, or added to the required set,
    which a tree published before it lacks. Consumers: every Milō update, and
    the update guard's sync back."""
    required, added = _script_array("MANIFEST_ARRAYS"), _script_array("MANIFEST_ARRAYS_ADDED")
    assert required == FROZEN_MANIFEST_ARRAYS, (
        f"milo-deploy-update requires {required}: a tree of an earlier release declares "
        f"{FROZEN_MANIFEST_ARRAYS} and nothing else, and a new array belongs in MANIFEST_ARRAYS_ADDED"
    )
    assert not set(added) & set(required), added


def test_an_array_added_later_reads_as_empty_in_an_older_tree(tmp_path):
    """The rollback to a release that predates an array still applies the
    rest; a tree that declares it is read as declared."""
    harness = _harness(tmp_path)
    harness.write_text(harness.read_text() + "MANIFEST_ARRAYS_ADDED=(SYSTEM_LATER)\n")
    newer = tmp_path / "newer.env"
    newer.write_text(MANIFEST.read_text() + "\nSYSTEM_LATER=(one two)\n")

    counts = []
    for tree in (MANIFEST, newer):
        result = subprocess.run(
            ["bash", "-c", f'set -euo pipefail; source "{harness}"; MANIFEST="{tree}"; '
                           'read_system_manifest; echo "${#SYSTEM_LATER[@]} ${#SYSTEM_APT_PACKAGES[@]}"'],
            capture_output=True, text=True, timeout=60, check=False,
        )
        assert result.returncode == 0, result.stderr
        counts.append(result.stdout.split())
    assert counts[0][0] == "0" and counts[1][0] == "2", counts
    assert int(counts[0][1]) > 0 and counts[0][1] == counts[1][1], counts


def test_the_two_writers_of_cmdline_txt_take_the_same_lock():
    """milo-set-wifi-country and the manifest's apply each rename a new
    cmdline.txt over the old one, from what they read: run at once, the second
    rename loses the first one's write — the WiFi country, or the managed
    tokens. The lock is the only thing between them, spelled in both scripts:
    a path moved in one alone drops the exclusion with no error."""
    country_text = WIFI_COUNTRY.read_text()
    deploy = re.findall(r"^CMDLINE_LOCK=(\S+)$", DEPLOY.read_text(), re.MULTILINE)
    country = re.findall(r'^LOCK_FILE="([^"]+)"$', country_text, re.MULTILINE)
    assert len(deploy) == 1 and len(country) == 1, (deploy, country)
    assert deploy == country, f"milo-deploy-update locks {deploy[0]}, milo-set-wifi-country {country[0]}"
    assert 'exec 9>"$LOCK_FILE"' in country_text and "flock -w" in country_text
    assert 'exec 9>"$CMDLINE_LOCK"' in _deploy_functions()["apply_cmdline"]


def test_a_unit_systemctl_refuses_is_named_on_the_last_line(tmp_path, manifest):
    """The last line is what the guard and the update screen show: it carries
    systemctl's own reason, which names the unit, not the list it was given."""
    unit = manifest["SYSTEM_UNITS_ENABLED"][-1]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "systemctl").write_text(
        f'#!/bin/bash\n[ "$1" = enable ] || exit 0\n'
        f'echo "Failed to enable unit: Unit file {unit} does not exist." >&2\nexit 1\n'
    )
    (bin_dir / "systemctl").chmod(0o755)

    result = _run(tmp_path, "apply_unit_states", path=str(bin_dir))

    assert result.returncode != 0
    last = result.stderr.strip().splitlines()[-1]
    assert f"Unit file {unit} does not exist" in last and len(last) < 200, last


def test_a_manifest_missing_an_array_is_refused(tmp_path):
    """A declaration renamed in the manifest and not in the script would read
    as empty — nothing to install, nothing to enable — and pass in silence."""
    broken = tmp_path / "broken.env"
    broken.write_text(MANIFEST.read_text().replace("SYSTEM_UNITS_MASKED=(", "SYSTEM_MASKED_UNITS=("))
    result = _run(tmp_path, "true", manifest=broken)
    assert result.returncode != 0
    assert "declares no SYSTEM_UNITS_MASKED array" in result.stderr
