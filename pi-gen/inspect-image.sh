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

cleanup() {
    [[ -n "$MNT" ]] && { umount "$MNT/boot/firmware" 2>/dev/null; umount "$MNT" 2>/dev/null; rmdir "$MNT" 2>/dev/null; }
    [[ -n "$LOOP" ]] && losetup -d "$LOOP" 2>/dev/null
}
trap cleanup EXIT

if [[ "${1:-}" == "--img" ]]; then
    IMG="${2:?usage: $0 --img <image.img>}"
    [[ -f "$IMG" ]] || { echo "no such image: $IMG" >&2; exit 126; }
    LOOP=$(losetup --show -fP "$IMG") || exit 126
    MNT=$(mktemp -d)
    # pi-gen lays out p1 = boot (FAT), p2 = root (ext4).
    mount "${LOOP}p2" "$MNT" || exit 126
    mkdir -p "$MNT/boot/firmware"
    mount "${LOOP}p1" "$MNT/boot/firmware" || exit 126
    ROOT="$MNT"
elif [[ -n "${1:-}" ]]; then
    ROOT="${1%/}/"
fi

BOOT="${ROOT%/}/boot/firmware"
[[ -d "$BOOT" ]] || BOOT="${ROOT%/}/boot"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

pass=0; fail=0
ok()   { printf '  \033[0;32mok\033[0m    %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  \033[0;31mFAIL\033[0m  %s\n' "$1"; fail=$((fail+1)); }
check() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1"; fi; }

section() { printf '\n\033[1m%s\033[0m\n' "$1"; }

echo "Inspecting: $ROOT   (boot: $BOOT)"
echo "Against the repo at: $REPO"

# --------------------------------------------------------------------------
section "Kernel command line"
# --------------------------------------------------------------------------
CMDLINE="$BOOT/cmdline.txt"
if [[ -f "$CMDLINE" ]]; then
    line=$(cat "$CMDLINE")
    # `root=` is the one that makes the card unbootable if the cleanup sed in
    # configure_cmdline ever widens by accident.
    check "cmdline keeps root="            "grep -q 'root=' '$CMDLINE'"
    check "cmdline keeps rootwait"         "grep -q 'rootwait' '$CMDLINE'"
    check "cmdline has quiet splash"       "grep -q 'quiet' '$CMDLINE' && grep -q 'splash' '$CMDLINE'"
    check "cmdline has the Milo console"   "grep -q 'console=tty3' '$CMDLINE'"
    # The token that drifted: FR was baked into the image, 00 into the installer.
    check "regdom is the world domain (00)" "grep -q 'cfg80211.ieee80211_regdom=00' '$CMDLINE'"
    check "regdom appears exactly once"    "[ \$(grep -o 'ieee80211_regdom=' '$CMDLINE' | wc -l) -eq 1 ]"
else
    bad "cmdline.txt not found under $BOOT"
fi
check "config.txt has silent boot" "grep -q 'disable_splash=1' '$BOOT/config.txt'"

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
for policy in milo-backend milo-ir-remote milo-client; do
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
# applies the last match, so the `milo` withdrawal lives in milo-backend and
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
section "Units enabled at boot"
# --------------------------------------------------------------------------
# Derived from what pi-gen enables, so a unit added there is checked the day it
# is added rather than when someone remembers to edit this list.
mapfile -t WANTED < <(grep -hE '^\s*systemctl enable ' "$REPO/pi-gen/stage-milo/03-configure/01-run.sh" \
                      | sed -E 's/.*enable +//; s/\.service$//; s/;$//')
[[ ${#WANTED[@]} -ge 10 ]] || bad "only ${#WANTED[@]} units parsed from the pi-gen enable list"
for u in "${WANTED[@]}"; do
    if compgen -G "${ROOT%/}/etc/systemd/system/*.wants/$u.service" >/dev/null \
       || compgen -G "${ROOT%/}/etc/systemd/system/*.target.wants/$u.service" >/dev/null; then
        ok "enabled: $u"
    else
        bad "enabled: $u (no .wants symlink)"
    fi
done
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
# milo-readiness is the only thing that ever quits Plymouth; these must stay masked.
for u in plymouth-quit plymouth-quit-wait; do
    check "$u.service masked" "[ \"\$(readlink '${ROOT%/}/etc/systemd/system/$u.service')\" = /dev/null ]"
done

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
check "ALSA only — no pipewire" "! [ -x '${ROOT%/}/usr/bin/pipewire' ]"
check "ALSA only — no pulseaudio" "! [ -x '${ROOT%/}/usr/bin/pulseaudio' ]"

# --------------------------------------------------------------------------
section "Configuration written from the single sources of truth"
# --------------------------------------------------------------------------
check "asound.conf deployed"        "cmp -s '$REPO/rootfs/etc/asound.conf' '${ROOT%/}/etc/asound.conf'"
check "shairport-sync.conf deployed" "cmp -s '$REPO/rootfs/etc/shairport-sync.conf' '${ROOT%/}/etc/shairport-sync.conf'"
check "avahi-daemon.conf deployed"  "cmp -s '$REPO/rootfs/etc/avahi/avahi-daemon.conf' '${ROOT%/}/etc/avahi/avahi-daemon.conf'"
check "nginx site written"          "grep -q 'server_name milo.local' '${ROOT%/}/etc/nginx/sites-available/milo'"
check "go-librespot uses avahi"     "grep -q 'zeroconf_backend: avahi' '${ROOT%/}/var/lib/milo/go-librespot/config.yml'"
check "navidrome config written"    "[ -f '${ROOT%/}/var/lib/milo/navidrome/navidrome.toml' ]"
check "qobuz config is ASCII-named" "grep -q 'name: Milo' '${ROOT%/}/var/lib/milo/qobuz/config.yaml'"
check "snapserver.conf written"     "[ -f '${ROOT%/}/etc/snapserver.conf' ]"
check "routing.env written"         "[ -f '${ROOT%/}/var/lib/milo/routing.env' ]"
check "snapclient.env written"      "[ -f '${ROOT%/}/var/lib/milo/snapclient.env' ]"
check "mac.env written"             "[ -f '${ROOT%/}/var/lib/milo/mac.env' ]"
# Seeding this is what crash-looped the backend with SchemaVersionMismatch. Only
# meaningful on an image: on a unit in service the backend has legitimately
# written it from the setup wizard.
if [[ "$ROOT" == "/" ]]; then
    printf '  \033[0;33mskip\033[0m  hardware.json NOT seeded (only checked on an image)\n'
else
    check "hardware.json NOT seeded" "! [ -f '${ROOT%/}/var/lib/milo/hardware.json' ]"
fi
check "polkit rule deployed"        "[ -f '${ROOT%/}/etc/polkit-1/rules.d/50-milo-networkmanager.rules' ]"
check "journald drop-in deployed"   "[ -f '${ROOT%/}/etc/systemd/journald.conf.d/99-milo-journald.conf' ]"
check "persistent journal dir"      "[ -d '${ROOT%/}/var/log/journal' ]"
check "snd-aloop two cards"         "grep -q 'id=Loopback,LoopbackDLNA' '${ROOT%/}/etc/modprobe.d/snd-aloop.conf'"

printf '\n\033[1m%d ok, %d failed\033[0m\n' "$pass" "$fail"
[[ $fail -eq 0 ]] || exit $(( fail > 125 ? 125 : fail ))
