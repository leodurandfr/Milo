#!/usr/bin/env bash
# ALSA routing smoke test for Milo.
#
# Verifies the audio chain is structurally consistent across:
#   - /etc/asound.conf            (Loopback subdevice layout)
#   - CamillaDSP config.yml       (capture device matches DSP slot)
#   - /etc/snapserver.conf        (snapserver source devices match per-source slots)
#   - aplay -L                    (asound.conf parses, _direct aliases enumerate)
#
# Catches: subdevice renumbering, alias renames, dead-PCM accumulation,
# divergence between asound.conf and the components that read from / write to it.
#
# Static checks run by default. Pass --with-live to additionally probe each
# alias with `aplay --dump-hw-params` against `/dev/null`: opens the PCM, prints
# the hw-params block, exits without ever starting a transfer. `-t raw` skips
# the WAV-header parse so /dev/null EOF is fine. No samples are written, so the
# amp stays silent. Probes that hit a busy subdevice (an active source already
# holds the slot) are reported as BUSY, not FAIL.
#
# Exit code: 0 = all OK, 1 = one or more failures, 2 = bad arguments / missing
# prerequisites.

set -u

# --- Configuration: expected layout (must match docs/plans/routing-refactor.md) -

# slot -> source name (used in asound.conf alias and snapserver.conf source name)
declare -A EXPECTED_SLOT_TO_SOURCE=(
    [1]="bluetooth"
    [2]="roc"
    [3]="spotify"
    [4]="radio"
    [5]="podcast"
    [6]="airplay"
    [7]="cd"
)

# source name -> snapserver-conf display name (capitalisation matters in the conf)
declare -A SNAPSERVER_DISPLAY_NAME=(
    [bluetooth]="Bluetooth"
    [roc]="ROC"
    [spotify]="Spotify"
    [radio]="Radio"
    [podcast]="Podcast"
    [airplay]="AirPlay"
    [cd]="CD"
)

ASOUND_CONF="${ASOUND_CONF:-/etc/asound.conf}"
CAMILLA_CONF="${CAMILLA_CONF:-/var/lib/milo/camilladsp/config.yml}"
SNAPSERVER_CONF="${SNAPSERVER_CONF:-/etc/snapserver.conf}"
EXPECTED_DSP_CAPTURE="plughw:Loopback,1,0"

# --- Argument parsing -----------------------------------------------------------

WITH_LIVE=0
for arg in "$@"; do
    case "$arg" in
        --with-live) WITH_LIVE=1 ;;
        -h|--help)
            sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            exit 2
            ;;
    esac
done

# --- Reporting helpers ----------------------------------------------------------

FAILURES=0
WARNINGS=0
declare -a ROWS  # each entry: "STATUS|CHECK|DETAIL"

record() {
    local status="$1" check="$2" detail="$3"
    ROWS+=("${status}|${check}|${detail}")
    case "$status" in
        FAIL) FAILURES=$((FAILURES + 1)) ;;
        WARN|BUSY) WARNINGS=$((WARNINGS + 1)) ;;
    esac
}

# --- Prerequisite checks --------------------------------------------------------

for f in "$ASOUND_CONF" "$CAMILLA_CONF" "$SNAPSERVER_CONF"; do
    if [[ ! -r "$f" ]]; then
        echo "ERROR: required file not readable: $f" >&2
        exit 2
    fi
done

if ! command -v aplay >/dev/null 2>&1; then
    echo "ERROR: aplay not found in PATH (alsa-utils not installed?)" >&2
    exit 2
fi

# --- Check 1: aplay -L parses asound.conf ---------------------------------------

if APLAY_OUTPUT=$(aplay -L 2>&1); then
    record OK "aplay -L parses ${ASOUND_CONF}" "exit 0"
else
    record FAIL "aplay -L parses ${ASOUND_CONF}" "exit non-zero: ${APLAY_OUTPUT}"
fi

# --- Check 2: each source has _direct + _multiroom aliases in asound.conf -------

for slot in "${!EXPECTED_SLOT_TO_SOURCE[@]}"; do
    src="${EXPECTED_SLOT_TO_SOURCE[$slot]}"
    for mode in direct multiroom; do
        alias="milo_${src}_${mode}"
        if grep -qE "^pcm\.${alias}[[:space:]]*\{" "$ASOUND_CONF"; then
            record OK "alias defined in asound.conf" "$alias"
        else
            record FAIL "alias defined in asound.conf" "$alias missing"
        fi
    done
done

# --- Check 3: _direct aliases enumerate via aplay -L ----------------------------
# (the dynamic milo_<source> alias resolves to _direct when MILO_MODE is unset)

for slot in "${!EXPECTED_SLOT_TO_SOURCE[@]}"; do
    src="${EXPECTED_SLOT_TO_SOURCE[$slot]}"
    alias="milo_${src}_direct"
    if grep -qE "^${alias}$" <<<"$APLAY_OUTPUT"; then
        record OK "alias enumerated by aplay -L" "$alias"
    else
        record FAIL "alias enumerated by aplay -L" "$alias not in aplay -L output"
    fi
done

# --- Check 4: _multiroom subdevice numbers match expected layout ----------------
# Parse the subdevice line that follows each pcm.milo_<src>_multiroom block.

for slot in "${!EXPECTED_SLOT_TO_SOURCE[@]}"; do
    src="${EXPECTED_SLOT_TO_SOURCE[$slot]}"
    alias="milo_${src}_multiroom"
    # Pull the first `subdevice N` after the alias declaration.
    actual=$(awk -v target="pcm.${alias}" '
        $0 ~ "^"target"[[:space:]]*\\{" { in_block=1; depth=1; next }
        !in_block { next }
        $1 == "subdevice" { print $2; exit }
        /\{/ { depth++ }
        /\}/ { depth--; if (depth==0) exit }
    ' "$ASOUND_CONF")
    if [[ -z "$actual" ]]; then
        record FAIL "${alias} subdevice resolves" "no subdevice line found"
    elif [[ "$actual" == "$slot" ]]; then
        record OK "${alias} subdevice = ${slot}" "expected"
    else
        record FAIL "${alias} subdevice = ${slot}" "actual=${actual}"
    fi
done

# --- Check 5: pcm.camilladsp uses Loopback subdevice 0 --------------------------

camilla_alsa_subdevice=$(awk '
    /^pcm\.camilladsp[[:space:]]*\{/ { in_block=1; depth=1; next }
    !in_block { next }
    $1 == "subdevice" { print $2; exit }
    /\{/ { depth++ }
    /\}/ { depth--; if (depth==0) exit }
' "$ASOUND_CONF")
if [[ "$camilla_alsa_subdevice" == "0" ]]; then
    record OK "pcm.camilladsp subdevice = 0 (DSP slot)" "expected"
else
    record FAIL "pcm.camilladsp subdevice = 0 (DSP slot)" "actual=${camilla_alsa_subdevice:-<missing>}"
fi

# --- Check 6: CamillaDSP config.yml capture device matches DSP slot -------------

camilla_capture=$(awk '
    /^[[:space:]]+capture:/ { in_capture=1; next }
    in_capture && $1 == "device:" {
        line=$0
        sub(/^[^"]*"/, "", line)
        sub(/".*$/, "", line)
        print line
        exit
    }
' "$CAMILLA_CONF")
if [[ "$camilla_capture" == "$EXPECTED_DSP_CAPTURE" ]]; then
    record OK "camilladsp capture = ${EXPECTED_DSP_CAPTURE}" "expected"
else
    record FAIL "camilladsp capture = ${EXPECTED_DSP_CAPTURE}" "actual=${camilla_capture:-<missing>}"
fi

# --- Check 7: snapserver source devices match expected layout -------------------

for slot in "${!EXPECTED_SLOT_TO_SOURCE[@]}"; do
    src="${EXPECTED_SLOT_TO_SOURCE[$slot]}"
    display="${SNAPSERVER_DISPLAY_NAME[$src]}"
    expected_device="hw:1,1,${slot}"
    # Find the source line for this display name, then check it carries the
    # expected device. Don't assume parameter order — snapserver query strings
    # don't guarantee a fixed order.
    source_line=$(grep -E "^source = alsa://.*[?&]name=${display}([&[:space:]]|$)" "$SNAPSERVER_CONF" || true)
    if [[ -z "$source_line" ]]; then
        record FAIL "snapserver source ${display} = ${expected_device}" "no line matching name=${display}"
    elif grep -qE "[?&]device=${expected_device}([&[:space:]]|$)" <<<"$source_line"; then
        record OK "snapserver source ${display} = ${expected_device}" "expected"
    else
        record FAIL "snapserver source ${display} = ${expected_device}" "actual: ${source_line}"
    fi
done

# --- Check 8 (optional): live --dump-hw-params probe ----------------------------

if [[ "$WITH_LIVE" -eq 1 ]]; then
    # Probe a single alias, return one of: OK / BUSY / FAIL <msg>.
    # `--dump-hw-params` opens the PCM and prints params without ever starting
    # a transfer — strictly safer than a real playback open, since `_direct`
    # aliases route through CamillaDSP to the amplifier and an open/close cycle
    # could otherwise toggle the I2S clock and produce an audible click.
    # `-t raw` skips the WAV-header parse on the (empty) /dev/null input.
    probe_alias() {
        local alias="$1"
        local out
        if out=$(timeout 2 aplay -t raw -f S16_LE -r 48000 -c 2 --dump-hw-params -D "$alias" /dev/null 2>&1); then
            echo "OK"
            return
        fi
        if grep -qiE "device or resource busy|in use" <<<"$out"; then
            echo "BUSY"
        else
            echo "FAIL ${out//$'\n'/ | }"
        fi
    }

    for slot in "${!EXPECTED_SLOT_TO_SOURCE[@]}"; do
        src="${EXPECTED_SLOT_TO_SOURCE[$slot]}"
        for mode in direct multiroom; do
            alias="milo_${src}_${mode}"
            result=$(probe_alias "$alias")
            case "$result" in
                OK) record OK "live probe ${alias}" "device opens" ;;
                BUSY) record BUSY "live probe ${alias}" "subdevice busy (active writer)" ;;
                *) record FAIL "live probe ${alias}" "${result#FAIL }" ;;
            esac
        done
    done
fi

# --- Summary --------------------------------------------------------------------

printf '\n%-6s  %-50s  %s\n' "STATUS" "CHECK" "DETAIL"
printf '%s\n' "------------------------------------------------------------------------------------------"
for row in "${ROWS[@]}"; do
    IFS='|' read -r status check detail <<<"$row"
    printf '%-6s  %-50s  %s\n' "$status" "$check" "$detail"
done

printf '\n%d failure(s), %d warning(s).\n' "$FAILURES" "$WARNINGS"

if [[ "$FAILURES" -gt 0 ]]; then
    exit 1
fi
exit 0
