#!/bin/bash
# Milo - Navidrome Installation (Music Library catalog engine)
#
# Installs Navidrome, the always-on catalog engine behind the Music Library
# source (Family C). Navidrome indexes whatever is mounted under /media/milo and
# exposes a localhost Subsonic API; the music_library source browses it and mpv
# streams `stream?id=…&format=raw` over localhost — the exact shape as the
# Podcast source, with Navidrome standing in for Podcast Index. See
# docs/plans/music-library.md.
#
# Navidrome ships as a single static Go binary (~35 MB), installed the same way
# as go-librespot / camilladsp: download the pinned arm64 release into
# /usr/local/bin. The service-account password is NOT baked into the image — it
# is generated per-device on first boot by milo-navidrome-provision, so the
# config here is user-agnostic and safe to bake.
#
# Can be sourced from install.sh or run standalone.

set -e

MILO_USER="${MILO_USER:-milo}"
MILO_DATA_DIR="${MILO_DATA_DIR:-/var/lib/milo}"

# Version pin — bump consciously (single source of truth shared with pi-gen's
# audio stage, which downloads the same tag).
NAVIDROME_VERSION="${NAVIDROME_VERSION:-0.63.2}"

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/common.sh"
fi

install_navidrome() {
    log_info "Installing Navidrome..."

    local temp_dir
    temp_dir=$(mktemp -d) || { log_error "Failed to create temp directory"; return 1; }
    register_temp_dir "$temp_dir"
    pushd "$temp_dir" > /dev/null

    wget -q "https://github.com/navidrome/navidrome/releases/download/v${NAVIDROME_VERSION}/navidrome_${NAVIDROME_VERSION}_linux_arm64.tar.gz"
    tar -xzf "navidrome_${NAVIDROME_VERSION}_linux_arm64.tar.gz" navidrome
    sudo cp navidrome /usr/local/bin/
    sudo chmod +x /usr/local/bin/navidrome

    popd > /dev/null

    configure_navidrome

    log_success "Navidrome installed"
}

# Write /var/lib/milo/navidrome/navidrome.toml and prepare the data + mount dirs.
# Kept separate from the binary download so the pi-gen image build reuses this as
# the single source of truth (pi-gen downloads the binary in its own audio stage,
# then calls configure_navidrome) — the same split as go-librespot. Only static,
# device-agnostic settings live here; the generated admin password comes from the
# first-boot provisioning env file, never this config.
configure_navidrome() {
    # DataFolder (DB + cache) under /var/lib/milo so backup/restore captures it.
    sudo mkdir -p "$MILO_DATA_DIR/navidrome"
    # Where the music actually is: one Navidrome library per mount under this
    # root, created and retired by the backend (backend/sources/music_library/
    # libraries.py) so the UI can browse one storage space at a time.
    sudo mkdir -p /media/milo
    # MusicFolder below points here, NOT at /media/milo. Navidrome demands a
    # MusicFolder and pins the library it creates from it as undeletable, so a
    # MusicFolder on the mount root would permanently index every mount a second
    # time, alongside the per-mount libraries. An empty directory retires it.
    sudo mkdir -p "$MILO_DATA_DIR/navidrome/default-library"

    sudo tee "$MILO_DATA_DIR/navidrome/navidrome.toml" > /dev/null << 'EOF'
# Navidrome config for Milō (Music Library catalog engine).
# Device-agnostic — safe to bake into the image. The service-account password is
# generated per-device on first boot (milo-navidrome-provision) and injected via
# ND_DEVAUTOCREATEADMINPASSWORD, NOT written here.

# Deliberately an EMPTY directory, not /media/milo. Navidrome creates one
# library from this path and refuses to ever delete it; the real libraries are
# created per mount (USB / SMB / NFS) under /media/milo by the backend, and a
# library on the mount root would index all of them a second time.
MusicFolder = "/var/lib/milo/navidrome/default-library"
# DB + cache; under /var/lib/milo so Milō backup/restore includes it.
DataFolder = "/var/lib/milo/navidrome"

# Localhost only — internal service, honours "local network only". The backend
# proxies browse/cover/stream; the frontend never hits :4533 directly.
Address = "127.0.0.1"
Port = 4533

# The folder watcher is left at its default (on, Scanner.WatcherWait = "5s";
# setting that to 0 is what would disable it). Kept because it costs nothing and
# does catch a local edit, but it is weaker than it looks and nothing here may
# rely on it: inotify reports neither writes on the far side of a CIFS/NFS mount
# NOR a mount landing on a watched directory. Everything under /media/milo
# arrives by mount, so the watcher never announces a storage space appearing.
# Scans are driven from the backend instead (see [Scanner] below).

# Do NOT turn bundled .m3u/.nsp files into playlists. Navidrome defaults this ON,
# which silently mints a playlist per sidecar file found in the library — and
# ripped/downloaded albums routinely ship a per-album .m3u tracklist, so the
# Playlists tab fills with dozens of "Auto-imported from '…m3u'" entries (and
# duplicates on rescan) that the user never made. Playlists on Milō are created
# and owned through the app's own UI, so this import path is pure noise here.
AutoImportPlaylists = false

# Appliance is offline-first and LAN-only: no anonymous usage telemetry.
EnableInsightsCollector = false

# Online metadata/art agents (artist images, bios) are intentionally always on —
# there is no user toggle. This is Navidrome's default; setting it explicitly makes
# the intent durable against a future default change. Album covers still come from
# embedded/folder art first; the online agents only enrich what those lack, and a
# failed call (offline) falls back silently — no functional regression when LAN-only.
# Independent of EnableInsightsCollector above: we fetch art but send no telemetry.
EnableExternalServices = true

# Cover-art file matching. Navidrome's patterns are Go filepath.Match globs run
# against the lowercased filename (core/artwork/sources.go), so a glob must match
# the WHOLE name — the default `front.*` only matches a file literally named
# `front.<ext>`. Ripped/self-tagged albums commonly ship `NN - Front.jpg` or
# `<Album> front.jpg`, which the default silently ignores (no cover). We append
# loose `*front*`/`*cover*`/`*folder*`/`*album*` as a LAST resort — after the
# exact names and embedded art — so those albums get their real cover while
# albums that already match an earlier rule are unaffected (zero regression).
# `*album*` catches Windows/foobar-style `AlbumArt.jpg` and `AlbumArt_{GUID}_*.jpg`;
# it stays safe because Navidrome only scans the album's OWN folder for external
# art, so "album"-named files there are always this album's cover. `*back*` is
# deliberately omitted (never surface the back cover as album art).
CoverArtPriority = "cover.*, folder.*, front.*, embedded, *front*, *cover*, *folder*, *album*"

LogLevel = "info"

# EVERY key below must sit under [Scanner]: 0.63.2 reads Scanner.Schedule and
# Scanner.ScanOnStartup, and a key written at the file's top level is dropped in
# silence — no warning, no deprecation notice. A top-level `ScanSchedule = "1h"`
# lived here for a fortnight doing nothing at all; the boot log said "Periodic
# scan is DISABLED" the whole time. Adding a key here: check it against the
# pinned binary's own log line, never against the docs.
[Scanner]

# Navidrome must NOT scan on its own at startup. Its libraries live under
# /media/milo, which the backend mounts a few seconds into its own startup — so a
# self-triggered boot scan reads the mountpoint while it is still an empty
# directory, and an empty directory is how Navidrome spells "every one of these
# tracks is gone". It flags the whole library missing, the Subsonic API filters
# missing files, and the source serves an empty catalog. The backend's own
# post-mount trigger cannot save it either: it lands mid-scan and Navidrome
# answers "already scanning". Boot scanning belongs to whoever knows when the
# storage is ready, which is the backend, not Navidrome.
ScanOnStartup = false

# Periodic incremental rescan — the catch-all, and the reason ScanOnStartup can
# be turned off safely. It is the only mechanism that ever notices (a) music
# added straight onto a NAS, which no inotify event reports, and (b) a mount the
# backend's trigger missed, whatever the reason. mtime-based: measured at 7s over
# 2419 tracks on a CIFS share with nothing changed. An unmounted storage space
# has no directory at all (milo-umount rmdir's the mountpoint), and Navidrome
# skips a library whose path does not exist without touching its tracks — so an
# hourly pass over a sleeping NAS is harmless. That asymmetry between "directory
# empty" and "directory absent" is what makes this safe; do not paper a
# mountpoint over with a placeholder file.
Schedule = "1h"

# Purge files that disappeared (an unplugged USB drive, a removed share) so they
# don't linger as empty "ghost" albums in the catalog. "full" purges only on an
# explicit full scan (triggered on removal events), so a transient NAS outage
# during a quick or scheduled scan never deletes still-valid tracks.
PurgeMissing = "full"

# Album identity: group tracks into one album by album title alone (a MusicBrainz
# album id still wins when present). Dropping album-artist from the key is what
# lets an untagged "Various Artists" compilation — every track a different ARTIST,
# no shared ALBUMARTIST/COMPILATION tag — collapse into ONE album instead of one
# album per track; a multi-disc set likewise stays a single album whether its
# discs sit in one folder or in CD1/CD2 subfolders (discs split by DISCNUMBER).
# Tradeoff: two genuinely different albums that share the exact same title with
# no MusicBrainz id merge (e.g. two "Greatest Hits") — rare, and softened by the
# id tier plus the UI showing the per-track artist. Changing this needs a FULL
# rescan, which Navidrome forces automatically when the PID config changes.
[PID]
Album = "musicbrainz_albumid|album"
EOF

    # milo owns the whole tree so milo-navidrome.service (User=milo) can read the
    # config, write the DB/cache, and generate the cred file on first boot.
    sudo chown -R "$MILO_USER:$MILO_USER" "$MILO_DATA_DIR/navidrome"
    sudo chown "$MILO_USER:$MILO_USER" /media/milo
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_navidrome
fi
