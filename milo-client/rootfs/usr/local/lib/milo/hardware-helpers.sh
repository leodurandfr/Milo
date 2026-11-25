#!/bin/bash
# Shared helpers for milo-apply-hardware and milo-client-apply-hardware.
#
# Twin of rootfs/usr/local/lib/milo/hardware-helpers.sh — server and
# satellite are deployed from separate trees, same logic on both.
#
# Callers must set:
#   HARDWARE_FILE  — path to hardware.json
#   CONFIG_FILE    — path to config.txt (set by resolve_config_file)

# Exactly the overlay set of backend/hardware/registry.py::AUDIO_CARDS — pinned by
# backend/tests/architecture/test_audio_overlay_allowlist.py, because a card added
# there and not here is rejected right before the reboot that would apply it.
VALID_AUDIO_OVERLAYS="hifiberry-dacplus-std hifiberry-dacplus-pro hifiberry-dacplusadcpro hifiberry-amp4pro hifiberry-amp100 hifiberry-dacplushd"

# --- JSON reader ---
read_json() {
    python3 -I -c "
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
keys = sys.argv[2].split('.')
val = data
for k in keys:
    val = val.get(k, '') if isinstance(val, dict) else ''
print(val if val is not None else '')
" "$HARDWARE_FILE" "$1"
}

# --- Managed-block removal ---
# The blank lines before the block go with it, since its writer puts one there,
# and any other run of blank lines is squeezed to one: removing BEGIN..END alone
# left one more blank line per apply (38 of the server's 109 lines). Rewritten
# through a sibling renamed over config.txt, as sed -i does.
remove_block() {
    local tag="$1" tmp="$CONFIG_FILE.milo-tmp"
    cp "$CONFIG_FILE" "$tmp"
    awk -v begin="# BEGIN $tag" -v end="# END $tag" '
        skip { if ($0 == end) skip = 0; next }
        $0 == begin { blank = 0; skip = 1; next }
        $0 == "" { blank = 1; next }
        { if (blank) print ""; blank = 0; print }
    ' "$CONFIG_FILE" > "$tmp"
    mv -f "$tmp" "$CONFIG_FILE"
}

# --- The managed blocks of each role ---
# Exactly the blocks each role's apply script writes. A unit changes role in
# place (milo-first-boot) and each apply removes only its own blocks, so the
# role change clears the other role's, and nothing else ever would: the unit
# would boot the card of the role it left, alongside its own once that role's
# apply runs, and a satellite would keep the server's display setup (the DSI
# panel overlay, or the forced HDMI mode and framebuffer size), claim the IR pin
# and drive the LED pin low for good.
SERVER_CONFIG_BLOCKS=("MILO AUDIO" "MILO SCREEN" "MILO IR" "MILO POWER LED")
CLIENT_CONFIG_BLOCKS=("MILO CLIENT AUDIO")

remove_role_blocks() {
    local tag tags
    case "$1" in
        server) tags=("${SERVER_CONFIG_BLOCKS[@]}") ;;
        client) tags=("${CLIENT_CONFIG_BLOCKS[@]}") ;;
        *) echo "ERROR: Unknown role '$1'" >&2; return 1 ;;
    esac
    for tag in "${tags[@]}"; do
        remove_block "$tag"
    done
}

# --- The config.txt path ---
# The boot partition, mounted here since Bookworm, and only here: no Milō image
# has another layout, and what Raspberry Pi OS leaves under /boot is a stub
# saying the file moved, which a write would reach in silence while the next
# boot reads the real one. Same rule as milo-deploy-update's boot_file.
resolve_config_file() {
    CONFIG_FILE="/boot/firmware/config.txt"
    if [[ ! -f "$CONFIG_FILE" ]]; then
        echo "ERROR: $CONFIG_FILE not found -- is the boot partition mounted at /boot/firmware?" >&2
        return 1
    fi
}

# --- Validate inputs ---
validate_hardware_json() {
    if [[ ! -f "$HARDWARE_FILE" ]]; then
        echo "ERROR: $HARDWARE_FILE not found" >&2
        exit 1
    fi
    if ! python3 -I -c "import json, sys; json.load(sys.stdin)" < "$HARDWARE_FILE" 2>/dev/null; then
        echo "ERROR: $HARDWARE_FILE is not valid JSON" >&2
        exit 1
    fi
    if [[ ! -f "$CONFIG_FILE" ]]; then
        echo "ERROR: $CONFIG_FILE not found" >&2
        exit 1
    fi
}

validate_audio_overlay() {
    local overlay="$1"
    if [[ -n "$overlay" ]]; then
        # Whole-word match, not `grep -w`: a dash is a word boundary to grep, so
        # -w accepted "hifiberry-dacplus" against "hifiberry-dacplus-std".
        if [[ " $VALID_AUDIO_OVERLAYS " != *" $overlay "* ]]; then
            echo "ERROR: Unknown audio overlay '$overlay'" >&2
            exit 1
        fi
    fi
}

# --- Backup config.txt (keep 3 most recent) ---
backup_config() {
    cp "$CONFIG_FILE" "$CONFIG_FILE.backup.$(date +%Y%m%d_%H%M%S)"
    ls -1t "$CONFIG_FILE".backup.* 2>/dev/null | tail -n +4 | xargs -r rm -f
}

# --- Disable HDMI audio on the vc4-kms-v3d overlay (preserves existing options) ---
disable_vc4_audio() {
    local file="$CONFIG_FILE"

    if grep -q "vc4-kms-v3d" "$file"; then
        if ! grep -q "vc4-kms-v3d.*noaudio" "$file"; then
            # Append ,noaudio (preserving any existing options)
            sed -i 's/\(dtoverlay=vc4-kms-v3d.*\)/\1,noaudio/' "$file"
        fi
    fi
}
