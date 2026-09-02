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
# The binary is downloaded by pi-gen/stage-milo/01-install-audio; this file only
# writes the config. Sourced by pi-gen/stage-milo during the image build, and by
# milo-navidrome-config.service on every boot.

set -e

MILO_USER="${MILO_USER:-milo}"
MILO_DATA_DIR="${MILO_DATA_DIR:-/var/lib/milo}"

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

# Artist photos. The default is "artist.*, album/artist.*, external"; `external`
# is dropped here so Navidrome only ever serves artist art the user actually
# shipped, and Milō owns the online tier (backend/sources/music_library/
# artist_images.py). Not a preference — the upstream agent picks the wrong
# person. It searches Deezer by name and keeps the FIRST hit whose name matches,
# while Deezer's search is not ordered by popularity: the first "Amy Winehouse"
# is a 741-fan duplicate with no photo, the real one (3.8 M fans) is second.
# Measured on a 108-artist library, 25 artists wore someone else's face and a
# dozen wore Deezer's grey silhouette, which is a genuine image no byte-level
# rule downstream can tell from a photo. Deezer returns `nb_fan` on every hit and
# Navidrome ignores it; ranking by it resolves 105 of those 108 correctly.
# The local tiers stay first, so a user's own artist.jpg still wins over both.
ArtistArtPriority = "artist.*, album/artist.*"

# One group per letter. Navidrome's default ends "... W X-Z(XYZ)", the Subsonic
# convention of folding the three thin letters into one bucket — which reaches
# the UI as a literal "X-Z" heading in the Artists list and a three-character
# rung on its A-Z rail, where every other rung is one letter. The buckets ARE
# the rail (frontend/src/components/music-library/ArtistIndexRail.vue draws the
# names this returns, never a hardcoded alphabet), so the fix belongs here and
# nowhere else. Empty groups are not returned, so the letters nobody has cost
# nothing.
#
# The default's "[Unknown]([)" goes for the same reason: it is a NINE-character
# bucket name, and the rail's gutter is 32px (24 on the panel). Dropped, a name
# starting with "[" falls to Navidrome's own "#" bucket, where every name that
# matches no group already goes — so every rung is one character by
# construction, which is the whole point of the line.
IndexGroups = "A B C D E F G H I J K L M N O P Q R S T U V W X Y Z"

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

# Periodic incremental rescan — the backstop, and the reason ScanOnStartup can be
# turned off safely. It catches a mount whose own trigger was lost, whatever the
# reason, so no failure of the paths above can outlive it.
#
# Deliberately NOT the freshness mechanism: the source asks for a scan when the
# user opens the library (source.py::_do_start), which is both the moment
# freshness matters and a moment the NAS is about to be woken by playback anyway.
# That is what lets this run every 6h instead of hourly — a scan over a sleeping
# NAS spins its disks up for nothing, and doing that 24 times a day to catch
# music the user is not currently looking for is a poor trade.
#
# mtime-based: measured at 7s over 2419 tracks on a CIFS share with nothing
# changed. An unmounted storage space has no directory at all (milo-umount
# rmdir's the mountpoint), and Navidrome skips a library whose path does not
# exist without touching its tracks — so a pass over a sleeping NAS is harmless
# beyond the spin-up. That asymmetry between "directory empty" and "directory
# absent" is what makes this safe; do not paper a mountpoint over with a
# placeholder file.
Schedule = "6h"

# Navidrome's own default, and the one it documents for removable and network
# storage: a file that disappeared is *marked* missing, never deleted. Marking is
# all Milo needs — the Subsonic API already excludes a missing track from every
# answer (measured: 721 albums returned for a library holding 764, the 43 marked
# ones absent), so a deleted file leaves the UI on the next scan whether or not
# its row is purged. Purging only decides the fate of rows nobody can see.
#
# "full" was set here to stop missing tracks lingering as "ghost" albums; they
# never reached the UI in the first place, so that bought nothing — and cost the
# only thing purging can cost. A full scan is global: it drops every track
# Navidrome cannot see right now, so one run while a USB key is unplugged threw
# away an index that was still valid, along with its stars, play counts and
# playlist entries (which is the reason Navidrome defaults to "never"). The
# whole apparatus that used to contain that risk — an offline gate on the scan
# route, a "blocked" status, a deferred-cleanup banner — existed only because of
# this line, and went with it.
#
# Consequence to keep in mind: nothing prunes the rows any more. They are
# invisible and cost only database size; a storage space that is removed for good
# still takes its rows with it, because forgetting a key or deleting a share
# deletes the whole Navidrome library (libraries.py).
PurgeMissing = "never"

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

