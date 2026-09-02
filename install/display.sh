#!/bin/bash
# Milo - Kernel command line
#
# Writes /boot/firmware/cmdline.txt from the parameter list install/boot-common.sh
# declares — the single source of truth for it. Everything else the display stack
# needs (seatd, cage, the cursor theme, the Plymouth splash, lightdm, the
# brightness tools) is done by pi-gen/stage-milo/03-configure directly.
#
# Sourced by pi-gen/stage-milo during the image build.

set -e

MILO_USER="${MILO_USER:-milo}"

configure_cmdline() {
    local boot_params="$1"
    local cmdline_file="/boot/firmware/cmdline.txt"
    [[ ! -f "$cmdline_file" ]] && cmdline_file="/boot/cmdline.txt"

    if [[ ! -f "$cmdline_file" ]]; then
        log_error "cmdline.txt not found"
        return 1
    fi

    cp "$cmdline_file" "${cmdline_file}.milo-backup" 2>/dev/null || true

    # Clean current cmdline (remove parameters we will set)
    local current_cmdline
    current_cmdline=$(cat "$cmdline_file")
    current_cmdline=$(echo "$current_cmdline" | sed -E '
        s/console=serial[0-9],[0-9]+//g
        s/console=tty[0-9]//g
        s/loglevel=[0-9]+//g
        s/\bquiet\b//g
        s/\bsplash\b//g
        s/plymouth\.[^ ]*//g
        s/logo\.[^ ]*//g
        s/vt\.[^ ]*//g
        s/fbcon=[^ ]*//g
        s/video=[^ ]*//g
        s/cfg80211\.[^ ]*//g
        s/  +/ /g
    ' | xargs)

    echo "${current_cmdline} ${boot_params}" | tr -s ' ' | tee "$cmdline_file" > /dev/null
    log_success "cmdline.txt configured"
}
