#!/usr/bin/env bash
# Multiroom state-coherence smoke test for Milo.
#
# Verifies that the four surfaces tracking "is multiroom enabled?" stay in
# agreement under stress:
#   - /var/lib/milo/settings.json      routing.multiroom_enabled (persistent SoT)
#   - /var/lib/milo/routing.env        MILO_MODE                 (derived, read by systemd)
#   - milo-snapserver-multiroom        ActiveState               (live systemd)
#   - milo-snapclient-multiroom        ActiveState               (live systemd)
#
# Coherence rule (post Phase 4 of docs/plans/multiroom-state-desync.md):
#   multiroom_enabled = true   ⇔ MILO_MODE=multiroom AND snapserver=active AND snapclient=active
#   multiroom_enabled = false  ⇔ MILO_MODE=direct    AND snapserver=inactive AND snapclient=inactive
#
# Stress scenarios:
#   - PHASE A: N rapid PUT /api/routing/multiroom toggles, coherence asserted after each.
#   - PHASE B (with --kill-test, requires sudo): kill -9 the backend mid-toggle,
#     wait for systemd to restart it, assert system reconciles to settings.json.
#
# Exit code: 0 = all OK, 1 = one or more failures, 2 = bad args / missing prereqs.

set -u

# --- Configuration --------------------------------------------------------------

SETTINGS_FILE="${SETTINGS_FILE:-/var/lib/milo/settings.json}"
ROUTING_ENV_FILE="${ROUTING_ENV_FILE:-/var/lib/milo/routing.env}"
SNAPSERVER_UNIT="milo-snapserver-multiroom.service"
SNAPCLIENT_UNIT="milo-snapclient-multiroom.service"
BACKEND_UNIT="milo-backend.service"
API_BASE="${API_BASE:-http://localhost:8000}"

TOGGLE_COUNT=20
KILL_TEST=0
SETTLE_SECONDS=2   # time we allow snapcast services to converge after a toggle

# --- Argument parsing -----------------------------------------------------------

for arg in "$@"; do
    case "$arg" in
        --kill-test) KILL_TEST=1 ;;
        --count=*)   TOGGLE_COUNT="${arg#--count=}" ;;
        -h|--help)
            sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'
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
        WARN) WARNINGS=$((WARNINGS + 1)) ;;
    esac
}

# --- Prerequisite checks --------------------------------------------------------

for tool in curl python3 systemctl; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "ERROR: required tool not found in PATH: $tool" >&2
        exit 2
    fi
done

for f in "$SETTINGS_FILE" "$ROUTING_ENV_FILE"; do
    if [[ ! -r "$f" ]]; then
        echo "ERROR: required file not readable: $f" >&2
        exit 2
    fi
done

if ! curl -sf -o /dev/null --max-time 3 "${API_BASE}/api/ping"; then
    echo "ERROR: backend not responding at ${API_BASE}/api/ping" >&2
    exit 2
fi

if [[ "$KILL_TEST" -eq 1 && "$(id -u)" -ne 0 ]]; then
    echo "ERROR: --kill-test requires root (uses systemctl kill / restart)" >&2
    exit 2
fi

# --- State readers --------------------------------------------------------------

read_settings_multiroom() {
    # Prints "true" or "false". Coerces missing/invalid to "false" (matches
    # _validate_and_merge default).
    python3 -c '
import json, sys
try:
    with open(sys.argv[1]) as f:
        data = json.load(f)
    val = data.get("routing", {}).get("multiroom_enabled", False)
    print("true" if bool(val) else "false")
except Exception:
    print("false")
' "$SETTINGS_FILE"
}

read_routing_env_mode() {
    # Prints "direct", "multiroom", or empty on missing.
    grep -E '^MILO_MODE=' "$ROUTING_ENV_FILE" 2>/dev/null | tail -n1 | cut -d= -f2
}

unit_is_active() {
    # Prints "active" or "inactive" (collapses "activating"/"deactivating"
    # into "active" for the period we care about — they hold the ALSA
    # device).
    local state
    state=$(systemctl is-active "$1" 2>/dev/null || true)
    case "$state" in
        active|activating|reloading) echo active ;;
        *)                            echo inactive ;;
    esac
}

# --- Coherence check ------------------------------------------------------------

# Asserts the four surfaces agree. Args: label (free-text context).
check_coherence() {
    local label="$1"
    local settings mode server client expected_mode expected_server expected_client

    settings=$(read_settings_multiroom)
    mode=$(read_routing_env_mode)
    server=$(unit_is_active "$SNAPSERVER_UNIT")
    client=$(unit_is_active "$SNAPCLIENT_UNIT")

    case "$settings" in
        true)
            expected_mode="multiroom"
            expected_server="active"
            expected_client="active"
            ;;
        false)
            expected_mode="direct"
            expected_server="inactive"
            expected_client="inactive"
            ;;
        *)
            record FAIL "[$label] settings parse" "got=$settings"
            return 1
            ;;
    esac

    local fail=0
    [[ "$mode"   == "$expected_mode"   ]] || { record FAIL "[$label] routing.env MILO_MODE" "want=$expected_mode got=$mode (settings=$settings)";   fail=1; }
    [[ "$server" == "$expected_server" ]] || { record FAIL "[$label] $SNAPSERVER_UNIT"        "want=$expected_server got=$server (settings=$settings)"; fail=1; }
    [[ "$client" == "$expected_client" ]] || { record FAIL "[$label] $SNAPCLIENT_UNIT"        "want=$expected_client got=$client (settings=$settings)"; fail=1; }

    if [[ "$fail" -eq 0 ]]; then
        record OK "[$label] coherent" "settings=$settings mode=$mode server=$server client=$client"
    fi
    return $fail
}

# --- API caller -----------------------------------------------------------------

set_multiroom() {
    local enabled="$1"
    local http_code
    http_code=$(curl -s -o /tmp/milo-multiroom-toggle.body -w '%{http_code}' \
        --max-time 30 \
        -X PUT "${API_BASE}/api/routing/multiroom" \
        -H 'Content-Type: application/json' \
        --data "{\"enabled\":${enabled}}" 2>/dev/null || echo "000")
    if [[ "$http_code" != "200" ]]; then
        echo "PUT /api/routing/multiroom enabled=${enabled} → HTTP ${http_code}: $(head -c 200 /tmp/milo-multiroom-toggle.body 2>/dev/null)"
        return 1
    fi
    return 0
}

# --- Initial snapshot -----------------------------------------------------------

echo "Initial state:"
echo "  settings.routing.multiroom_enabled = $(read_settings_multiroom)"
echo "  routing.env MILO_MODE              = $(read_routing_env_mode)"
echo "  $SNAPSERVER_UNIT      = $(unit_is_active "$SNAPSERVER_UNIT")"
echo "  $SNAPCLIENT_UNIT      = $(unit_is_active "$SNAPCLIENT_UNIT")"
echo

INITIAL_STATE=$(read_settings_multiroom)
check_coherence "initial" || true

# --- PHASE A: rapid toggles -----------------------------------------------------

echo "PHASE A: ${TOGGLE_COUNT} rapid toggles via API"
target_state="$INITIAL_STATE"
for ((i = 1; i <= TOGGLE_COUNT; i++)); do
    if [[ "$target_state" == "true" ]]; then
        target_state="false"
    else
        target_state="true"
    fi

    if err=$(set_multiroom "$target_state"); then
        record OK "[A#${i}] PUT enabled=${target_state}" "HTTP 200"
    else
        record FAIL "[A#${i}] PUT enabled=${target_state}" "$err"
        continue
    fi

    # Allow snapcast units to converge. The API returns after the routing
    # service has issued start/stop, but systemd transitions are not synchronous.
    sleep "$SETTLE_SECONDS"

    check_coherence "A#${i}" || true
done

# --- PHASE B: kill -9 mid-toggle ------------------------------------------------

if [[ "$KILL_TEST" -eq 1 ]]; then
    echo
    echo "PHASE B: kill -9 backend mid-toggle"

    # Target state we want settings to land on (flip from current).
    current=$(read_settings_multiroom)
    if [[ "$current" == "true" ]]; then
        target_state="false"
    else
        target_state="true"
    fi

    # Fire the toggle in the background; it might or might not complete its
    # full transition before we kill the backend. Backend should reconcile on
    # restart regardless.
    (
        curl -s -o /dev/null --max-time 15 \
            -X PUT "${API_BASE}/api/routing/multiroom" \
            -H 'Content-Type: application/json' \
            --data "{\"enabled\":${target_state}}" 2>/dev/null || true
    ) &
    toggle_pid=$!

    # Give the backend a short window to start the transition, then SIGKILL.
    sleep 0.3
    systemctl kill -s SIGKILL "$BACKEND_UNIT" || true
    wait "$toggle_pid" 2>/dev/null || true

    # Wait for systemd to restart the unit and the API to come back.
    echo "  waiting for ${BACKEND_UNIT} to restart and /api/ping to respond..."
    deadline=$((SECONDS + 60))
    while (( SECONDS < deadline )); do
        if curl -sf -o /dev/null --max-time 2 "${API_BASE}/api/ping"; then
            break
        fi
        sleep 1
    done

    if ! curl -sf -o /dev/null --max-time 2 "${API_BASE}/api/ping"; then
        record FAIL "[B] backend restart" "API still down after 60s"
    else
        record OK "[B] backend restart" "API responding"

        # Let _detect_initial_state + _sync_snapcast_state run, plus snapcast
        # unit startup. Backend init is typically <5s, snapcast <5s on top.
        sleep 10

        # Whatever settings.json says now, all surfaces should match it.
        check_coherence "B post-restart" || true
    fi
fi

# --- Restore initial state ------------------------------------------------------

current=$(read_settings_multiroom)
if [[ "$current" != "$INITIAL_STATE" ]]; then
    echo
    echo "Restoring initial state (multiroom_enabled=${INITIAL_STATE})..."
    set_multiroom "$INITIAL_STATE" >/dev/null || true
    sleep "$SETTLE_SECONDS"
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
