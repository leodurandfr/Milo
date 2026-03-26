#!/bin/bash
# Shared helpers for milo-apply-hardware and milo-client-apply-hardware.
#
# Callers must set:
#   HARDWARE_FILE  — path to hardware.json
#   CONFIG_FILE    — path to config.txt (set by resolve_config_file)

VALID_AUDIO_OVERLAYS="hifiberry-dacplus-std hifiberry-amp4pro hifiberry-amp100 hifiberry-dac hifiberry-dacplushd hifiberry-dacplus"

# --- JSON reader ---
read_json() {
    python3 -c "
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
remove_block() {
    local tag="$1"
    sed -i "/^# BEGIN $tag$/,/^# END $tag$/d" "$CONFIG_FILE"
}

# --- Resolve config.txt path (with Pi OS fallback) ---
resolve_config_file() {
    CONFIG_FILE="/boot/firmware/config.txt"
    if [[ ! -f "$CONFIG_FILE" ]]; then
        CONFIG_FILE="/boot/config.txt"
    fi
}

# --- Validate inputs ---
validate_hardware_json() {
    if [[ ! -f "$HARDWARE_FILE" ]]; then
        echo "ERROR: $HARDWARE_FILE not found" >&2
        exit 1
    fi
    if ! python3 -c "import json, sys; json.load(sys.stdin)" < "$HARDWARE_FILE" 2>/dev/null; then
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
        if ! echo "$VALID_AUDIO_OVERLAYS" | grep -qw "$overlay"; then
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

# --- Disable GPU audio on vc4 overlays (preserves existing options) ---
disable_vc4_audio() {
    local file="$CONFIG_FILE"

    # vc4-fkms-v3d → ,audio=off
    if grep -q "vc4-fkms-v3d" "$file"; then
        # Already has audio=off → skip
        if ! grep -q "vc4-fkms-v3d.*audio=off" "$file"; then
            # Replace audio=on with audio=off
            if grep -q "vc4-fkms-v3d.*audio=on" "$file"; then
                sed -i 's/\(dtoverlay=vc4-fkms-v3d.*\)audio=on/\1audio=off/' "$file"
            else
                # Append ,audio=off (preserving any existing options)
                sed -i 's/\(dtoverlay=vc4-fkms-v3d[^,]*\)\(.*\)/\1\2,audio=off/' "$file"
            fi
        fi
    fi

    # vc4-kms-v3d → ,noaudio
    if grep -q "vc4-kms-v3d" "$file"; then
        if ! grep -q "vc4-kms-v3d.*noaudio" "$file"; then
            # Append ,noaudio (preserving any existing options)
            sed -i 's/\(dtoverlay=vc4-kms-v3d.*\)/\1,noaudio/' "$file"
        fi
    fi
}

# --- Remove legacy (non-managed) hifiberry overlays ---
remove_legacy_overlays() {
    local legacy_comment="${1:-# Milo - HiFiBerry Audio}"
    for overlay in $VALID_AUDIO_OVERLAYS; do
        sed -i "/^dtoverlay=$overlay$/d" "$CONFIG_FILE"
    done
    sed -i "/^${legacy_comment}$/d" "$CONFIG_FILE"
}
