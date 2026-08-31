#!/bin/bash
# milo-wait-ready.sh — drive the Plymouth progress bar, then hand the screen over.
#
# Runs as root from milo-readiness.service, which milo-kiosk.service requires:
# Plymouth holds the screen until this exits, so the bar always completes before
# the UI appears. No sudoers entry — the backend never invokes it.
#
# Two things it owns that are not obvious:
#
#   * The bar. Plymouth's own estimator reads /var/lib/plymouth/boot-duration,
#     which measured 0.73 s against a ~13 s splash, so it filled inside the first
#     second and froze. Progress here is a real signal, ramped linearly between
#     milestones and capped one point short of each so a slow phase holds below
#     its milestone instead of overshooting it.
#
#   * The Chromium prewarm. Chromium is 339 MB on an SD card (~85 MB/s), and at
#     boot that read is ~4 s of black screen between Plymouth quitting and the
#     first painted frame — measured, since a kiosk restart with those pages
#     already cached paints in 1 s, profile wiped or not. Reading them while the
#     splash is up moves the read off the critical path. mq-deadline is the disk
#     scheduler here, so ionice would be decorative and is not used.

set -u

BACKEND_URL="http://localhost:8000/api/health"
FRONTEND_URL="http://localhost/"
DEADLINE=$((SECONDS + 45))     # fail open: show the UI even if a service never answers
TICK=0.25
TICKS_PER_S=4

log() { echo "[readiness] $1"; }

# No-op when Plymouth is not up (a manual `systemctl restart milo-kiosk` — the
# waits below still run, so the kiosk keeps starting after the backend).
progress() { plymouth system-update --progress="$1" 2>/dev/null || true; }

# Ramp the bar from $1 to $2 over an expected $3 seconds while `${@:4}` fails.
ramp_until() {
    local from=$1 to=$2 expected=$3 label=$4
    shift 4
    local span=$((to - from - 1)) ticks=0 pct started=$SECONDS

    while (( SECONDS < DEADLINE )); do
        if "$@"; then
            progress "$to"
            log "$label ready after $((SECONDS - started))s"
            return 0
        fi
        pct=$(( from + span * ticks / (expected * TICKS_PER_S) ))
        (( pct > to - 1 )) && pct=$((to - 1))
        progress "$pct"
        sleep "$TICK"
        ((ticks++))
    done

    log "$label did not answer within the deadline — showing the UI anyway"
    return 1
}

responds() { curl -sf -o /dev/null "$1"; }

# Started before the first wait so it uses the window where the backend has not
# begun reading yet; `timeout` bounds it against a stalled card.
timeout 30 cat /usr/lib/chromium/chromium /usr/lib/chromium/*.so \
    /usr/lib/chromium/*.pak /usr/lib/chromium/*.bin > /dev/null 2>&1 &
prewarm=$!

# Weights track the measured split: nginx answers ~4 s in, the backend ~13 s.
ramp_until 0 25 4 "nginx" responds "$FRONTEND_URL"
ramp_until 25 95 13 "backend" responds "$BACKEND_URL"

# The prewarm is an optimisation, never a gate. Waiting on it unbounded holds the
# splash at 95% for as long as the read takes, and a `cat` stuck in uninterruptible
# I/O past its own timeout would hold it past TimeoutStartSec — a oneshot killed
# there never reaches `plymouth quit`, and plymouth-quit.service is masked, so that
# is a splash frozen for good. Past the deadline the screen goes to the kiosk and
# the read finishes behind it.
while kill -0 "$prewarm" 2>/dev/null && (( SECONDS < DEADLINE )); do
    sleep "$TICK"
done

progress 100
sleep 0.4                      # let the bar glide to full before the screen changes
plymouth quit --retain-splash 2>/dev/null || true
log "splash handed over to the kiosk"

exit 0
