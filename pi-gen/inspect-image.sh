#!/bin/bash
# inspect-image.sh — assert a provisioned Milō root contains what it should.
#
# A green pi-gen build proves the stage scripts ran to completion. It does not
# prove the image is right: `set -e` catches a command that fails, never one
# that succeeds while doing the wrong thing — a config written to the wrong
# path, a helper copied without its exec bit, a unit installed but not enabled.
# Every check below is something this repo has actually got wrong at least once.
#
# Usage:
#   sudo ./inspect-image.sh                       # the running unit
#   sudo ./inspect-image.sh /mnt/milo-image       # a mounted image root
#   sudo ./inspect-image.sh --img milo.img        # mount the image, check, unmount
#
# --ref <tag-or-branch> names what the image was supposed to be built from, in any
# position. Given a release tag, pre-release included, the checkout inside the
# image must sit exactly on it: that is the only check that ties the artefact to
# the version it is published under. An unknown option, or an image and a root
# at once, is refused.
#
# INSPECT_PARSE_ONLY=1 prints what the arguments parsed to and exits before
# anything is mounted; test_release_channel.py replays the workflow's call with it.
#
# The default target is `/`, so the same checks that accept an image also
# accept a live appliance — which is how the checks themselves are validated.
#
# Exit code is the number of failures, capped at 125.

set -uo pipefail

ROOT="/"
BOOT=""
IMG=""
LOOP=""
MNT=""
REF=""

cleanup() {
    [[ -n "$MNT" ]] && { umount "$MNT/boot/firmware" 2>/dev/null; umount "$MNT" 2>/dev/null; rmdir "$MNT" 2>/dev/null; }
    [[ -n "$LOOP" ]] && losetup -d "$LOOP" 2>/dev/null
}
trap cleanup EXIT

refuse() { echo "$1" >&2; echo "usage: $0 [--ref <tag>] [--img <image.img> | <root>]" >&2; exit 126; }
# An option's value is never another option: `--ref --img x.img` would otherwise
# read as the ref "--img" and a root "x.img", and the tag check would vanish.
value() { [[ $# -ge 2 && -n "$2" && "$2" != -* ]] || refuse "$1 needs a value"; }

ROOT_ARG=""
while (($#)); do
    case $1 in
        --ref) value "$@"; [[ -z "$REF" ]] || refuse "--ref given twice"; REF="$2"; shift 2 ;;
        --img) value "$@"; [[ -z "$IMG" ]] || refuse "--img given twice"; IMG="$2"; shift 2 ;;
        -*) refuse "unknown option: $1" ;;
        *) [[ -z "$ROOT_ARG" ]] || refuse "one root only"; ROOT_ARG="$1"; shift ;;
    esac
done
[[ -z "$IMG" || -z "$ROOT_ARG" ]] || refuse "--img and a root are exclusive"
[[ -n "$ROOT_ARG" ]] && ROOT="${ROOT_ARG%/}/"

if [[ "${INSPECT_PARSE_ONLY:-}" == 1 ]]; then
    echo "REF=$REF"; echo "IMG=$IMG"; echo "ROOT=$ROOT"
    exit 0
fi

if [[ -n "$IMG" ]]; then
    [[ -f "$IMG" ]] || { echo "no such image: $IMG" >&2; exit 126; }
    LOOP=$(losetup --show -fP "$IMG") || exit 126
    MNT=$(mktemp -d)
    # pi-gen lays out p1 = boot (FAT), p2 = root (ext4).
    mount "${LOOP}p2" "$MNT" || exit 126
    mkdir -p "$MNT/boot/firmware"
    mount "${LOOP}p1" "$MNT/boot/firmware" || exit 126
    ROOT="$MNT"
fi

BOOT="${ROOT%/}/boot/firmware"
[[ -d "$BOOT" ]] || BOOT="${ROOT%/}/boot"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

pass=0; fail=0
ok()   { printf '  \033[0;32mok\033[0m    %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  \033[0;31mFAIL\033[0m  %s\n' "$1"; fail=$((fail+1)); }
check() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1"; fi; }
# A check that holds on a fresh image only: a unit in service may legitimately
# differ (its owner set it up), so a live root skips it.
image_check() {
    if [[ "$ROOT" == "/" ]]; then
        printf '  \033[0;33mskip\033[0m  %s (only checked on an image)\n' "$1"
    else
        check "$1" "$2"
    fi
}

section() { printf '\n\033[1m%s\033[0m\n' "$1"; }

echo "Inspecting: $ROOT   (boot: $BOOT)"
echo "Against the repo at: $REPO"

# --------------------------------------------------------------------------
section "Kernel command line"
# --------------------------------------------------------------------------
CMDLINE="$BOOT/cmdline.txt"
if [[ -f "$CMDLINE" ]]; then
    line=$(cat "$CMDLINE")
    # `root=` is the one that makes the card unbootable if the manifest's
    # writer (milo-deploy-update apply_cmdline) ever takes a token it does not own.
    check "cmdline keeps root="            "grep -q 'root=' '$CMDLINE'"
    check "cmdline keeps rootwait"         "grep -q 'rootwait' '$CMDLINE'"
    check "cmdline has quiet splash"       "grep -q 'quiet' '$CMDLINE' && grep -q 'splash' '$CMDLINE'"
    check "cmdline has the Milo console"   "grep -q 'console=tty3' '$CMDLINE'"
    # The token that drifted: FR was restated in the stage, 00 declared in the module.
    # Now the manifest's one seed: written where absent, never replaced.
    check "regdom is the world domain (00)" "grep -q 'cfg80211.ieee80211_regdom=00' '$CMDLINE'"
    check "regdom appears exactly once"    "[ \$(grep -o 'ieee80211_regdom=' '$CMDLINE' | wc -l) -eq 1 ]"
    # The device tree disables the memory controller ahead of this line; the last
    # memory token here must re-enable it, or every unit's MemoryMax= is inert.
    check "cmdline enables the memory cgroup" "[ \"\$(grep -o 'cgroup_[a-z]*=memory' '$CMDLINE' | tail -1)\" = cgroup_enable=memory ]"
else
    bad "cmdline.txt not found under $BOOT"
fi
check "config.txt has silent boot" "grep -q 'disable_splash=1' '$BOOT/config.txt'"
# Under [all], or a model filter keeps EEE on every other board; right after an
# empty dtoverlay=, or the last overlay's scope takes the parameter instead of
# the base device tree.
check "config.txt turns Ethernet EEE off, for every board" \
    "awk '/^\\[/{s=\$0} /^dtparam=eee=off\$/{print s \"|\" p} !/^#/ && NF {p=\$0}' '$BOOT/config.txt' | grep -qx '\\[all\\]|dtoverlay='"

# --------------------------------------------------------------------------
section "Helpers — both trees reach /usr/local"
# --------------------------------------------------------------------------
# The unified image carries both roles: milo-first-boot's conversion needs the
# client tree's helpers, and before this audit nothing but the image build ever
# put them there.
for tree_dir in "$REPO/rootfs/usr/local/bin" "$REPO/milo-client/rootfs/usr/local/bin"; do
    label=$([[ "$tree_dir" == *milo-client* ]] && echo client || echo server)
    for src in "$tree_dir"/*; do
        [[ -f "$src" ]] || continue
        name=$(basename "$src")
        target="${ROOT%/}/usr/local/bin/$name"
        if [[ -x "$target" ]]; then ok "$label helper $name"; else bad "$label helper $name (missing or not executable)"; fi
    done
done
check "shared hardware-helpers.sh" "[ -f '${ROOT%/}/usr/local/lib/milo/hardware-helpers.sh' ]"

# --------------------------------------------------------------------------
section "Sudoers — present, 0440, and parseable"
# --------------------------------------------------------------------------
for policy in milo-backend milo-ir-remote milo-account milo-client; do
    f="${ROOT%/}/etc/sudoers.d/$policy"
    if [[ -f "$f" ]]; then
        mode=$(stat -c '%a' "$f")
        [[ "$mode" == "440" ]] && ok "sudoers $policy (0440)" || bad "sudoers $policy has mode $mode, want 440"
        # A file sudo cannot parse is skipped, silently dropping every grant in it.
        visudo -c -f "$f" >/dev/null 2>&1 && ok "sudoers $policy parses" || bad "sudoers $policy fails visudo"
        cmp -s "$f" "$(ls "$REPO"/rootfs/etc/sudoers.d/$policy "$REPO"/milo-client/rootfs/etc/sudoers.d/$policy 2>/dev/null | head -1)" \
            && ok "sudoers $policy matches the repo" || bad "sudoers $policy differs from the repo"
    else
        bad "sudoers $policy missing"
    fi
done

# The blanket NOPASSWD Raspberry Pi OS grants the image's first user is taken
# back per *user*, not per file: sudo reads /etc/sudoers.d in lexical order and
# applies the last match, so the `milo` withdrawal lives in milo-backend (and in
# milo-account, read before it, which carries it on a converted satellite) and
# milo-ir-remote (read after) carries grants only. Checking each file alone
# reported milo-ir-remote as broken when it is correct by design.
for user in milo milo-client; do
    rules=""
    for f in $(ls "${ROOT%/}"/etc/sudoers.d/milo* 2>/dev/null | sort); do
        rules+=$(grep -E "^${user} " "$f" 2>/dev/null)$'\n'
    done
    w=$(printf '%s' "$rules" | grep -n 'PASSWD: ALL' | grep -v NOPASSWD | head -1 | cut -d: -f1)
    g=$(printf '%s' "$rules" | grep -n 'NOPASSWD:' | head -1 | cut -d: -f1)
    if [[ -z "$g" ]]; then
        ok "no grants for $user (nothing to withdraw)"
    elif [[ -n "$w" && "$w" -lt "$g" ]]; then
        ok "$user: the blanket NOPASSWD is withdrawn before any grant"
    else
        bad "$user: no PASSWD: ALL rule before the first grant — every grant is decorative"
    fi
done

# --------------------------------------------------------------------------
section "Release signing keys"
# --------------------------------------------------------------------------
# What the card's first update verifies the release it is offered with. Missing
# or keyless, every release is refused; another copy than the repo's, and the
# image trusts a key the tree does not list.
keys="${ROOT%/}/etc/milo/release-signing.pub"
check "release signing keys installed, as in the repo" "cmp -s '$keys' '$REPO/rootfs/etc/milo/release-signing.pub'"
check "release signing keys list an ed25519 key" \
    "grep -qE '^milo-release namespaces=\"milo-release\" ssh-ed25519 ' '$keys'"

# --------------------------------------------------------------------------
section "The system manifest"
# --------------------------------------------------------------------------
# What the image and every update apply from system-manifest.env, read from the
# repo: a unit, a package or a line added there is checked the day it is added.
# Packages and unit states only grow on a unit (nothing is removed), so these
# hold on a live appliance too.
# shellcheck source=/dev/null
if source "$REPO/system-manifest.env" 2>/dev/null && [[ ${#SYSTEM_UNITS_ENABLED[@]} -ge 10 ]]; then
    for u in "${SYSTEM_UNITS_ENABLED[@]}"; do
        if compgen -G "${ROOT%/}/etc/systemd/system/*.wants/$u" >/dev/null; then
            ok "enabled: $u"
        else
            bad "enabled: $u (no .wants symlink)"
        fi
    done
    for u in "${SYSTEM_UNITS_MASKED[@]}"; do
        check "masked: $u" "[ \"\$(readlink '${ROOT%/}/etc/systemd/system/$u')\" = /dev/null ]"
    done
    missing_packages=()
    for p in "${SYSTEM_APT_PACKAGES[@]}"; do
        [[ "$(dpkg-query --admindir="${ROOT%/}/var/lib/dpkg" -W -f='${db:Status-Status}' "$p" 2>/dev/null)" == installed ]] \
            || missing_packages+=("$p")
    done
    if [[ ${#missing_packages[@]} -eq 0 ]]; then
        ok "all ${#SYSTEM_APT_PACKAGES[@]} packages installed"
    else
        bad "packages not installed: ${missing_packages[*]}"
    fi
    managed=$(awk '$0 == "# BEGIN MILO SYSTEM" {on = 1; next} $0 == "# END MILO SYSTEM" {on = 0} on' "$BOOT/config.txt" 2>/dev/null)
    check "config.txt holds the manifest's block, as declared" \
          "[ \"\$managed\" = \"\$(printf '%s\\n' \"\${SYSTEM_CONFIG_TXT[@]}\")\" ]"
    for token in "${SYSTEM_CMDLINE[@]}"; do
        check "cmdline carries $token" "grep -qw -- '$token' '$BOOT/cmdline.txt'"
    done
else
    bad "system-manifest.env not read from $REPO"
fi
# A seed, not a manifest unit: milo-apply-hardware owns it once a screen is set.
image_check "enabled: milo-kiosk.service (the seed for a first boot)" \
    "compgen -G '${ROOT%/}/etc/systemd/system/*.wants/milo-kiosk.service'"
check "default target is graphical" \
      "readlink '${ROOT%/}/etc/systemd/system/default.target' | grep -q graphical"
# Their lifecycle belongs to AudioRoutingService alone; an [Install] symlink here
# is the state-desync class.
for u in milo-snapserver-multiroom milo-snapclient-multiroom; do
    if compgen -G "${ROOT%/}/etc/systemd/system/*.wants/$u.service" >/dev/null; then
        bad "$u must NOT be enabled (routing owns its lifecycle)"
    else
        ok "$u correctly not enabled"
    fi
done

# --------------------------------------------------------------------------
section "First-boot posture — what a stranger's card ships with"
# --------------------------------------------------------------------------
# Everything here is decided in `pi-gen/config`, i.e. consumed by *upstream*
# pi-gen stages that this repo does not contain. `test_image_defaults.py`
# asserts the file says the right thing; only an image can say what pi-gen did
# with it — and each of these failing silently is a unit that looks fine and is
# not.

# The factory password is identical on every unit Milō ships. That is safe only
# while SSH is shut: it is the one remote path that would accept it (neither
# nginx nor the backend authenticates anything). If `ENABLE_SSH=0` ever stops
# taking effect, every card ships with a fleet-wide credential on port 22 and
# nothing anywhere says so.
# Image-only, like hardware.json below: on a unit in service the owner may have
# opened SSH deliberately from Settings, which is the whole point of the switch.
if [[ "$ROOT" == "/" ]]; then
    printf '  \033[0;33mskip\033[0m  ssh.service not enabled (only checked on an image)\n'
elif compgen -G "${ROOT%/}/etc/systemd/system/*.wants/ssh.service" >/dev/null \
   || compgen -G "${ROOT%/}/etc/systemd/system/*.target.wants/ssh.service" >/dev/null; then
    bad "ssh.service is enabled — the shared factory password is reachable from the network"
else
    ok "ssh.service correctly not enabled"
fi

# The escape hatch the README documents: an empty `ssh` file on the boot
# partition — the way in when no UI can be reached, and into a satellite whose
# app predates its SSH switch. Shipped enabled by raspberrypi-sys-mods; if a
# stage ever disabled it, that instruction becomes a lie no test would catch.
check "sshswitch.service enabled (the boot-partition escape hatch)" \
      "compgen -G '${ROOT%/}/etc/systemd/system/*.wants/sshswitch.service' >/dev/null"

# The recovery AP is open, so sshd refuses whoever reaches it through the AP.
# The drop-in counts only while Debian's sshd_config still includes the directory.
check "sshd refuses recovery-AP clients (drop-in deployed)" \
      "cmp -s '$REPO/rootfs/etc/ssh/sshd_config.d/milo-recovery-ap.conf' '${ROOT%/}/etc/ssh/sshd_config.d/milo-recovery-ap.conf'"
check "sshd_config includes sshd_config.d" \
      "grep -qxF 'Include /etc/ssh/sshd_config.d/*.conf' '${ROOT%/}/etc/ssh/sshd_config'"

# SSH logs in as `milo`, and `sudo` on the unit needs a password that works. A
# locked or absent hash breaks both — the switch would open a door no password
# fits, and the account could not sudo at all.
if [[ -r "${ROOT%/}/etc/shadow" ]]; then
    check "the milo account carries a usable password hash" \
          "grep -q '^milo:\\\$' '${ROOT%/}/etc/shadow'"
else
    printf '  \033[0;33mskip\033[0m  milo password hash (/etc/shadow unreadable — run as root)\n'
fi

# Not a location: the value that means "nobody has told us yet". The frontend
# adopts a browser's zone only while the system still sits on it, comparing
# against `DEFAULT_TIMEZONE` in backend/api/system.py. A different string here
# is adoption that never fires, on every unit, in silence.
# Image-only for the same reason: a unit in service has had its zone adopted
# from the first browser that opened the UI, or set by hand.
if [[ "$ROOT" == "/" ]]; then
    printf '  \033[0;33mskip\033[0m  timezone is Etc/UTC (only checked on an image)\n'
else
    check "timezone is the neutral default (Etc/UTC)" \
          "[ \"\$(readlink '${ROOT%/}/etc/localtime')\" = /usr/share/zoneinfo/Etc/UTC ]"
fi

# The setup access point runs on 2.4 GHz channel 6 before any country is picked,
# which the world domain allows. A wpa_supplicant.conf pinning a country is the
# second answer to "where is this unit" that `pi-gen/config` deliberately no
# longer gives.
check "no country pinned in wpa_supplicant.conf" \
      "! [ -f '${ROOT%/}/etc/wpa_supplicant/wpa_supplicant.conf' ]"

# What upstream pi-gen and Raspberry Pi Imager change about the account and the
# name on a first boot. On a unit in service these legitimately differ (a
# satellite is milo-client, the server's own card predates the purge), so they
# are read on an image only (image_check).
# milo.local, the address the README and both apps open, is published from it.
image_check "hostname is milo" "[ \"\$(cat '${ROOT%/}/etc/hostname')\" = milo ]"
# Every path, sudoers policy and unit is written around this account; Imager's
# customization renames uid 1000 in place.
image_check "uid 1000 is milo, at /home/milo" \
    "[ \"\$(awk -F: '\$3 == 1000 {print \$1 \":\" \$6}' '${ROOT%/}/etc/passwd')\" = milo:/home/milo ]"
# export-image's `rename-user` re-enabled the tty8 wizard after stage-milo had
# disabled it, and multi-user.target waited on it forever (Bureau, 2026-11-06).
# The sshd drop-in is the same script's other trace.
image_check "userconfig.service not enabled (the tty8 wizard)" \
    "! compgen -G '${ROOT%/}/etc/systemd/system/*.wants/userconfig.service'"
image_check "no rename-user sshd drop-in" "! [ -e '${ROOT%/}/etc/ssh/sshd_config.d/rename_user.conf' ]"
# stage2 installs cloud-init and seeds it whatever ENABLE_CLOUD_INIT says; the
# server's own card carries both.
image_check "cloud-init not installed" "! [ -e '${ROOT%/}/usr/bin/cloud-init' ]"
for seed in user-data meta-data network-config; do
    image_check "no cloud-init $seed on the boot partition" "! [ -e '$BOOT/$seed' ]"
done
# The purge's autoremove must not reach it: network-manager depends on it.
image_check "netplan.io still installed" \
    "dpkg-query --admindir='${ROOT%/}/var/lib/dpkg' -W -f='\${db:Status-Abbrev}' netplan.io | grep -q '^ii'"

# The Bluetooth name. `hostnamectl hostname`, which raspi-config's hostname
# setter (and so an Imager customisation) calls, sets the pretty hostname too,
# so a rename clears it; milo-first-boot puts it back on a server through
# hostnamed, which quotes a value holding a space. Nothing on the unit changes
# it on purpose, so it is checked everywhere, by value.
pretty_hostname() { sed -n 's/^PRETTY_HOSTNAME=//p' "$1" | sed 's/^"\(.*\)"$/\1/'; }
BLUETOOTH_NAME=$(pretty_hostname "$REPO/rootfs/etc/machine-info")
check "machine-info names the repo's Bluetooth name ($BLUETOOTH_NAME)" \
      "[ -n \"\$BLUETOOTH_NAME\" ] && [ \"\$(pretty_hostname '${ROOT%/}/etc/machine-info')\" = \"\$BLUETOOTH_NAME\" ]"

# --------------------------------------------------------------------------
section "Unit files match the repo"
# --------------------------------------------------------------------------
for src in "$REPO"/system/*.service; do
    name=$(basename "$src")
    target="${ROOT%/}/etc/systemd/system/$name"
    if [[ ! -f "$target" ]]; then bad "unit $name not installed"
    elif cmp -s "$src" "$target"; then ok "unit $name"
    else bad "unit $name differs from the repo"; fi
done

# --------------------------------------------------------------------------
section "Boot splash"
# --------------------------------------------------------------------------
for f in "$REPO"/rootfs/usr/share/plymouth/themes/milo/*; do
    name=$(basename "$f")
    t="${ROOT%/}/usr/share/plymouth/themes/milo/$name"
    if cmp -s "$f" "$t"; then ok "theme $name"; else bad "theme $name missing or differs"; fi
done
# plymouth-set-default-theme writes [Daemon] Theme= into plymouthd.conf; the
# vendor defaults file still says ceratopsian and is not what is read.
check "plymouth default theme is milo" \
      "grep -q '^Theme=milo' '${ROOT%/}/etc/plymouth/plymouthd.conf'"
# milo-readiness is the only thing that ever quits Plymouth: the manifest masks
# plymouth-quit and plymouth-quit-wait, checked with its other units above.

# --------------------------------------------------------------------------
section "Binaries the appliance cannot run without"
# --------------------------------------------------------------------------
for b in go-librespot navidrome camilladsp snapserver snapclient shairport-sync nqptp cage chromium mpv; do
    if compgen -G "${ROOT%/}/usr/{bin,local/bin,sbin,local/sbin}/$b" >/dev/null 2>&1 \
       || [[ -x "${ROOT%/}/usr/bin/$b" || -x "${ROOT%/}/usr/local/bin/$b" || -x "${ROOT%/}/usr/sbin/$b" || -x "${ROOT%/}/usr/local/sbin/$b" ]]; then
        ok "binary $b"
    else
        bad "binary $b not found"
    fi
done
# Present is not enough: Debian's snapcast is a binary of the same name.
SNAPCAST_VERSION=$(sed -n 's/^SNAPCAST_VERSION=//p' "$REPO/dependencies.env" 2>/dev/null)
if [[ -z "$SNAPCAST_VERSION" ]]; then
    bad "SNAPCAST_VERSION not read from $REPO/dependencies.env"
else
    for p in snapserver snapclient; do
        check "$p is ${SNAPCAST_VERSION}-1, installed" \
              "[ \"\$(dpkg-query --admindir='${ROOT%/}/var/lib/dpkg' -W -f='\${db:Status-Abbrev}\${Version}' $p)\" = 'ii ${SNAPCAST_VERSION}-1' ]"
    done
fi
# configure maps localstatedir to ${prefix}/var once --prefix is given, and the
# unit's StateDirectory= only creates /var/lib/bluealsa.
check "bluealsa stores under /var/lib/bluealsa" \
      "strings -a '${ROOT%/}/usr/bin/bluealsa' | grep -qx /var/lib/bluealsa"
check "bluealsa has no /usr/var path" \
      "command -v strings && [ -f '${ROOT%/}/usr/bin/bluealsa' ] && ! strings -a '${ROOT%/}/usr/bin/bluealsa' | grep -q /usr/var"
# fdk-aac's license is GPL-incompatible and bluealsa links libbluetooth (GPL),
# so a bluealsa built with AAC is not redistributable.
check "no fdk-aac in the image (bluealsa built without AAC)" \
      "[ -f '${ROOT%/}/usr/bin/bluealsa' ] && ! compgen -G '${ROOT%/}/usr/lib/*/libfdk-aac.so*'"
# Tidal's SDK and iFi's device certificate are not ours to redistribute either:
# a unit builds the runtime tree itself the first time Tidal is selected, so a
# unit in service may hold it and only an image is refused for it.
image_check "no Tidal Connect runtime in the image (a unit fetches it at first use)" \
      "! compgen -G '${ROOT%/}/opt/milo/tidal-connect*'"
check "ALSA only — no pipewire" "! [ -x '${ROOT%/}/usr/bin/pipewire' ]"
check "ALSA only — no pulseaudio" "! [ -x '${ROOT%/}/usr/bin/pulseaudio' ]"

# --------------------------------------------------------------------------
section "Configuration written from the single sources of truth"
# --------------------------------------------------------------------------
check "asound.conf deployed"        "cmp -s '$REPO/rootfs/etc/asound.conf' '${ROOT%/}/etc/asound.conf'"
check "shairport-sync.conf deployed" "cmp -s '$REPO/rootfs/etc/shairport-sync.conf' '${ROOT%/}/etc/shairport-sync.conf'"
check "avahi-daemon.conf deployed"  "cmp -s '$REPO/rootfs/etc/avahi/avahi-daemon.conf' '${ROOT%/}/etc/avahi/avahi-daemon.conf'"
check "ARP sysctl deployed"         "cmp -s '$REPO/rootfs/etc/sysctl.d/90-milo-arp.conf' '${ROOT%/}/etc/sysctl.d/90-milo-arp.conf'"
check "link-down sysctl deployed"   "cmp -s '$REPO/rootfs/etc/sysctl.d/90-milo-linkdown.conf' '${ROOT%/}/etc/sysctl.d/90-milo-linkdown.conf'"
check "ethernet IPv4 wait deployed" "cmp -s '$REPO/rootfs/etc/NetworkManager/conf.d/90-milo-ethernet-ipv4.conf' '${ROOT%/}/etc/NetworkManager/conf.d/90-milo-ethernet-ipv4.conf'"
check "nginx site deployed"         "cmp -s '$REPO/rootfs/etc/nginx/sites-enabled/milo' '${ROOT%/}/etc/nginx/sites-enabled/milo'"
check "nginx default site removed"  "! [ -e '${ROOT%/}/etc/nginx/sites-enabled/default' ]"
# The recovery-AP gate reads the client from X-Forwarded-For; a proxied path
# without it hands the backend nginx's loopback address instead.
check "nginx names the client on every proxied path" \
      "[ \"\$(grep -cF 'proxy_pass http://milo_backend' '${ROOT%/}/etc/nginx/sites-enabled/milo')\" = \"\$(grep -cF 'X-Forwarded-For \$proxy_add_x_forwarded_for' '${ROOT%/}/etc/nginx/sites-enabled/milo')\" ]"
check "go-librespot uses avahi"     "grep -q 'zeroconf_backend: avahi' '${ROOT%/}/var/lib/milo/go-librespot/config.yml'"
check "navidrome config written"    "[ -f '${ROOT%/}/var/lib/milo/navidrome/navidrome.toml' ]"
check "qobuz speaker is named Milō" "grep -q 'name: Milō' '${ROOT%/}/var/lib/milo/qobuz/config.yaml'"
check "qobuz dir is private"        "[ \"\$(stat -c %a '${ROOT%/}/var/lib/milo/qobuz')\" = 700 ]"
check "snapserver.conf written"     "[ -f '${ROOT%/}/etc/snapserver.conf' ]"
check "routing.env written"         "[ -f '${ROOT%/}/var/lib/milo/routing.env' ]"
check "snapclient.env written"      "[ -f '${ROOT%/}/var/lib/milo/snapclient.env' ]"
check "mac.env written"             "[ -f '${ROOT%/}/var/lib/milo/mac.env' ]"
# Seeding this is what crash-looped the backend on a schema mismatch. Only
# meaningful on an image: on a unit in service the backend has legitimately
# written it from the setup wizard.
if [[ "$ROOT" == "/" ]]; then
    printf '  \033[0;33mskip\033[0m  hardware.json NOT seeded (only checked on an image)\n'
else
    check "hardware.json NOT seeded" "! [ -f '${ROOT%/}/var/lib/milo/hardware.json' ]"
fi
# Content, not presence: the rule grants NetworkManager to the `milo` user, so
# every Milō daemon holds it, and a stale copy could grant more or refuse the
# backend (the unit condition it once carried was measured to do exactly that).
check "polkit rule deployed"        "cmp -s '$REPO/rootfs/etc/polkit-1/rules.d/50-milo-networkmanager.rules' '${ROOT%/}/etc/polkit-1/rules.d/50-milo-networkmanager.rules'"
check "journald drop-in deployed"   "[ -f '${ROOT%/}/etc/systemd/journald.conf.d/99-milo-journald.conf' ]"
check "persistent journal dir"      "[ -d '${ROOT%/}/var/log/journal' ]"
check "snd-aloop options deployed"  "cmp -s '$REPO/rootfs/etc/modprobe.d/snd-aloop.conf' '${ROOT%/}/etc/modprobe.d/snd-aloop.conf'"
check "snd-aloop loaded at boot"    "cmp -s '$REPO/rootfs/etc/modules-load.d/snd-aloop.conf' '${ROOT%/}/etc/modules-load.d/snd-aloop.conf'"
check "connectivity check deployed" "cmp -s '$REPO/rootfs/etc/NetworkManager/conf.d/99-milo-connectivity.conf' '${ROOT%/}/etc/NetworkManager/conf.d/99-milo-connectivity.conf'"

# --------------------------------------------------------------------------
section "udev rules — deployed as in rootfs/"
# --------------------------------------------------------------------------
# The stage copies each rule by name, so a rule added to rootfs/ and not to the
# stage reaches no flashed card.
for f in "$REPO"/rootfs/etc/udev/rules.d/*.rules; do
    name=$(basename "$f")
    check "udev rule $name deployed" "cmp -s '$f' '${ROOT%/}/etc/udev/rules.d/$name'"
done

# --------------------------------------------------------------------------
section "Screen — no vendor tool"
# --------------------------------------------------------------------------
# Brightness is the backend writing the DSI panel's sysfs backlight, or
# milo-brightness-7 over hidraw for the USB panel: all either needs from the
# image is the permission 99-milo-screen.rules grants. The stage used to run
# Waveshare's install.sh as root, which put a Qt slider in
# /usr/share/applications, and wrote a second backlight rule of its own.
check "no vendor brightness tool in /usr/share/applications" \
    "! compgen -G '${ROOT%/}/usr/share/applications/brightness*'"
backlight_rules=$(grep -lF 'SUBSYSTEM=="backlight"' "${ROOT%/}"/etc/udev/rules.d/*.rules 2>/dev/null \
                  | xargs -r -n1 basename | tr '\n' ' ')
check "the backlight is granted by 99-milo-screen.rules alone" \
    "[ \"\$backlight_rules\" = '99-milo-screen.rules ' ]"

# --------------------------------------------------------------------------
section "The release this image carries"
# --------------------------------------------------------------------------
# What a user flashes and what the update flow later offers have to be the same
# thing. Until the frontend was built in CI these three were all unwatched: the
# image built its own `dist/` with `npm install`, the clone was pinned to one
# tag by its refspec, and nothing checked either.
MILO_CHECKOUT="${ROOT%/}/home/milo/milo"

check "frontend dist installed" "[ -f '$MILO_CHECKOUT/frontend/dist/index.html' ]"
# The written offer for GPL source has to accompany the binaries the image
# carries: in the checkout, and in the interface Settings › System opens it
# from, which the build copies from the checkout's (frontend/vite-plugins/).
check "third-party notices in the checkout" "[ -s '$MILO_CHECKOUT/THIRD_PARTY_NOTICES.md' ]"
check "third-party notices in the frontend dist, as in the checkout" \
    "cmp -s '$MILO_CHECKOUT/frontend/dist/THIRD_PARTY_NOTICES.md' '$MILO_CHECKOUT/THIRD_PARTY_NOTICES.md'"
# Not built here, so no build tree may be left behind — one would mean the stage
# fell back to compiling, which is the divergence this removed.
check "no frontend build tree"  "! [ -d '$MILO_CHECKOUT/frontend/node_modules' ]"
# The refspec a `--single-branch` tag clone leaves behind makes every later
# release unreachable from the update button. Measured, then fixed.
check "origin fetches all branches" \
    "git -C '$MILO_CHECKOUT' config --get-all remote.origin.fetch | grep -q 'refs/heads/\*'"
# Every other check here compares the image with this repository, so it is
# worth something only if the image cloned this very commit: a ref's name, read
# again by the chroot hours into the build, may have moved. Both checkouts
# belong to other users than root (sudo), hence the safe.directory.
image_check "checkout is the commit this repository is at" \
    "[ \"\$(git -c safe.directory='*' -C '$MILO_CHECKOUT' rev-parse HEAD)\" = \"\$(git -c safe.directory='*' -C '$REPO' rev-parse HEAD)\" ]"

if [[ -n "$REF" ]]; then
    # A suffixed tag (v0.2.0-rc1) is published too, as a pre-release, and the
    # stage seeds it the same way: the same two checks hold for it.
    if [[ "$REF" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
        check "checkout sits exactly on $REF" \
            "[ \"\$(git -C '$MILO_CHECKOUT' describe --tags --exact-match 2>/dev/null)\" = '$REF' ]"
        check "satellite identity seeded with $REF" \
            "[ \"\$(cat '${ROOT%/}/var/lib/milo-client/app-version' 2>/dev/null)\" = '$REF' ]"
    else
        printf '  \033[0;33mskip\033[0m  checkout ref (%s is not a release tag)\n' "$REF"
    fi
fi

printf '\n\033[1m%d ok, %d failed\033[0m\n' "$pass" "$fail"
[[ $fail -eq 0 ]] || exit $(( fail > 125 ? 125 : fail ))
