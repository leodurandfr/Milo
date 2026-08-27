# backend/core/updates/catalog.py
"""
Declares every program Milo ships: where its installed version is read from,
which upstream repo it comes from, and where its files live on the unit.

One entry per program, read by both VersionService (the version-discovery keys)
and UpdateService (the install-layout keys). It used to be two parallel dicts in
two files keyed by the same names, so adding a program meant editing both and
forgetting the second failed silently as "Update not supported".

Keys, per program:

  Version discovery
    name             UI label shown in the update manager.
    description       i18n key for the one-line subtitle.
    commands          how to read the installed version, per component.
    repo              GitHub "owner/name" the latest release is fetched from.
    version_regex     extracts the version out of both of the above.
    validated_version the version this Milō ships, read from the repo's
                      `dependencies.env` — the only place it is declared, shared
                      with both install trees and pi-gen. What the update flow
                      offers and installs, whatever upstream has released since
                      (see get_latest_github_version). `milo` alone has none: it
                      is the app, not a dependency.

  Install layout
    log_name          the program's own name, for log lines and error strings.
                      Not always `name`: Navidrome's UI label is "Music Library".
    binary_path       installed binary, and the target of install-binary.
    service_name      systemd unit stopped and restarted around the swap.
    backup_path       where the rollback copy is kept.
    asset_url         release asset to download, "{version}" substituted.
                      Its presence is what routes a program to the shared
                      _update_binary_program flow.
    tar_member        extract only this member (tarballs shipping docs).
    always_on         stop and restart the service unconditionally instead of
                      preserving whatever state it was in.
    config_path       config file backed up alongside the binary.
"""
from backend.core.updates.dependency_versions import DEPENDENCY_VERSIONS

PROGRAMS = {
    "milo": {
        "name": "Milō",
        "description": "updates.miloApp",
        "commands": {
            "main": ["git", "-C", "/home/milo/milo", "describe", "--tags", "--always"]
        },
        "repo": "leodurandfr/Milo",
        "version_regex": r"v?(\d+\.\d+\.\d+)",
        "git_path": "/home/milo/milo",
        "git_branch": "main",
        "service_name": "milo-backend.service",
        "backup_path": "/var/lib/milo/backups/milo-app"
    },
    "go-librespot": {
        "name": "go-librespot",
        "description": "updates.spotifyConnect",
        "commands": {
            # Version is embedded in binary as "B0.6.1" pattern (since v0.6.1+)
            "main": ["sh", "-c", "strings /usr/local/bin/go-librespot 2>/dev/null | grep -oE '^B[0-9]+\\.[0-9]+\\.[0-9]+$' | sed 's/^B//'"]
        },
        "repo": "devgianlu/go-librespot",
        "version_regex": r"(\d+\.\d+\.\d+)",
        "validated_version": DEPENDENCY_VERSIONS["GO_LIBRESPOT_VERSION"],
        "log_name": "go-librespot",
        "binary_path": "/usr/local/bin/go-librespot",
        "config_path": "/var/lib/milo/go-librespot/config.yml",
        "service_name": "milo-spotify.service",
        "backup_path": "/var/lib/milo/backups/go-librespot",
        "asset_url": "https://github.com/devgianlu/go-librespot/releases/download/v{version}/go-librespot_linux_arm64.tar.gz"
    },
    "shairport-sync": {
        "name": "AirPlay",
        "description": "updates.airplay",
        "commands": {
            "main": ["sh", "-c", "cat /var/lib/milo/shairport-sync-version 2>/dev/null || shairport-sync --version 2>&1"]
        },
        "repo": "mikebrady/shairport-sync",
        "version_regex": r"(\d+\.\d+(?:\.\d+)?)",
        "validated_version": DEPENDENCY_VERSIONS["SHAIRPORT_SYNC_VERSION"],
        "binary_path": "/usr/local/bin/shairport-sync",
        "service_name": "milo-airplay.service",
        "backup_path": "/var/lib/milo/backups/shairport-sync",
        "configure_flags": [
            "--sysconfdir=/etc", "--with-alsa", "--with-avahi",
            "--with-ssl=openssl", "--with-soxr", "--with-metadata",
            # --with-metadata already implies the pipe on 5.2.x, but the pipe
            # is the channel Milo reads AirPlay metadata from — name it rather
            # than inherit it.
            "--with-metadata-pipe",
            "--with-airplay-2", "--with-dbus-interface"
        ]
    },
    "multiroom": {
        "name": "Multiroom",
        "description": "updates.multiroom",
        "commands": {
            "snapserver": ["snapserver", "--version"],
            "snapclient": ["snapclient", "--version"]
        },
        "repo": "badaix/snapcast",
        "version_regex": r"v(\d+\.\d+\.\d+)",
        "validated_version": DEPENDENCY_VERSIONS["SNAPCAST_VERSION"],
        "services": [
            "milo-snapserver-multiroom.service",
            "milo-snapclient-multiroom.service"
        ],
        "backup_path": "/var/lib/milo/backups/multiroom"
    },
    "camilladsp": {
        "name": "CamillaDSP",
        "description": "updates.audioProcessor",
        "commands": {
            "main": ["/usr/local/bin/camilladsp", "--version"]
        },
        "repo": "HEnquist/camilladsp",
        "version_regex": r"(\d+\.\d+\.\d+)",
        "validated_version": DEPENDENCY_VERSIONS["CAMILLADSP_VERSION"],
        "log_name": "CamillaDSP",
        "binary_path": "/usr/local/bin/camilladsp",
        "service_name": "milo-camilladsp.service",
        "backup_path": "/var/lib/milo/backups/camilladsp",
        "asset_url": "https://github.com/HEnquist/camilladsp/releases/download/v{version}/camilladsp-linux-aarch64.tar.gz",
        "always_on": True
    },
    "qobuz-proxy": {
        "name": "Qobuz",
        "description": "updates.qobuz",
        "commands": {
            # Installed as a git-tag pip package in a venv (no --version CLI);
            # read the distribution metadata via the venv's own Python. Missing
            # venv → FileNotFoundError → status "not_installed" (no update offered).
            "main": ["/var/lib/milo/qobuz/venv/bin/python", "-c",
                     "import importlib.metadata as m; print(m.version('qobuz-proxy'))"]
        },
        "repo": "leolobato/qobuz-proxy",
        "version_regex": r"v?(\d+\.\d+\.\d+)",
        "validated_version": DEPENDENCY_VERSIONS["QOBUZ_PROXY_VERSION"],
        "service_name": "milo-qobuz.service",
        "venv_path": "/var/lib/milo/qobuz/venv",
        "backup_path": "/var/lib/milo/backups/qobuz"
    },
    "navidrome": {
        # Catalog engine behind the Music Library source. `navidrome --version`
        # prints "0.63.2 (hash)"; missing binary → FileNotFoundError → status
        # "not_installed" (no update offered).
        "name": "Music Library",
        "description": "updates.musicLibrary",
        "commands": {
            "main": ["/usr/local/bin/navidrome", "--version"]
        },
        "repo": "navidrome/navidrome",
        "version_regex": r"(\d+\.\d+\.\d+)",
        "validated_version": DEPENDENCY_VERSIONS["NAVIDROME_VERSION"],
        "log_name": "Navidrome",
        "binary_path": "/usr/local/bin/navidrome",
        "service_name": "milo-navidrome.service",
        "backup_path": "/var/lib/milo/backups/navidrome",
        "asset_url": "https://github.com/navidrome/navidrome/releases/download/v{version}/navidrome_{version}_linux_arm64.tar.gz",
        # The tarball ships README/LICENSE next to the binary.
        "tar_member": "navidrome",
        "always_on": True
    }
}
