"""The names a Milō app update leaves on disk, shared with its guard.

An update of the server's own app is carried out by the release that is
leaving (`UpdateService._update_milo_app`) and judged by the release that
arrives: after the reboot, `/usr/local/bin/milo-update-guard` runs before every
start of milo-backend.service (`ExecStartPre=-`) and either lets the new release
start, counts its start, or puts the previous release back. The two sides are
therefore always one release apart, exactly as on a satellite
(`milo-client-update-guard`), so every name below is frozen and append-only:
renaming one strands the unit whose guard reads the old name. The guard is a
standalone stdlib script and restates them; tests/architecture hold the two
copies equal.

The journal is the update's whole state. It is written before the first change
to the tree, moved forward after each step once that step is on the card, and
read by the guard at every start:

  step < "rebooting"      the install never finished: put the origin back now
  "failure" set           the updater recorded a failure: put the origin back now
  step == "rebooting"     the trial: count this start, roll back at MAX_ATTEMPTS
  step == "committed"     the new release held; the backend finishes the
                          dependency set and the satellites, then closes it

A journal is only ever acted on while HEAD is one of the two commits it names,
so a development checkout, which never has one, is never touched.
"""

# Where the journal and its companions live (milo-owned: StateDirectory=milo).
STATE_DIR = "/var/lib/milo/app-update"
JOURNAL_NAME = "journal.json"
ATTEMPTS_NAME = "attempts"
# The outcome of the last update, kept until the next one opens a journal; the
# Milō row in Settings › Updates reads it.
LAST_UPDATE_NAME = "last-update.json"

# What the update moved aside, on the same filesystem as the live copies so a
# restore is a rename. Cleared when a journal opens, so "a copy exists" means
# "this update replaced it". Never inside the checkout: an untracked directory
# there makes the next update's `git status --porcelain` refuse.
BACKUP_DIR = "/var/lib/milo/backups/milo-app"
KEPT_VENV_NAME = "venv"
KEPT_DIST_NAME = "dist"
DATA_SNAPSHOT_NAME = "data"
DISCARD_NAME = "discard"

# Journal keys.
KEY_ORIGIN_COMMIT = "origin_commit"
KEY_ORIGIN_TAG = "origin_tag"
KEY_TARGET_TAG = "target_tag"
KEY_TARGET_COMMIT = "target_commit"
KEY_STEP = "step"
KEY_FAILURE = "failure"
KEY_VENV_KEPT = "venv_kept"

# Steps, in order. Append-only: a release may add one at the end, never rename
# or reorder one.
STEPS = ("started", "checked_out", "installed", "synced", "swapped", "rebooting", "committed")
TRIAL_STEP = "rebooting"
COMMITTED_STEP = "committed"

# last-update.json keys and outcomes.
KEY_FROM = "from"
KEY_TO = "to"
KEY_OUTCOME = "outcome"
KEY_DETAIL = "detail"
OUTCOMES = ("committed", "rolled_back", "rollback_failed")

# The trial: MAX_ATTEMPTS - 1 real starts, the next one runs the previous
# release. A release holds once it has stayed up HOLD_S after listening,
# counted on the monotonic clock (no RTC: the wall clock steps during boot).
MAX_ATTEMPTS = 3
HOLD_S = 120

GUARD = "/usr/local/bin/milo-update-guard"

# The venv copy in progress: renamed to KEPT_VENV_NAME only once complete, so a
# copy cut by a power loss is never mistaken for a kept venv. A leftover is
# removed, never restored.
KEPT_VENV_PARTIAL_NAME = "venv.partial"

# The data snapshot: BACKUP_DIR/DATA_SNAPSHOT_NAME holds one copy per versioned
# file, and DATA_MANIFEST_NAME, written last, lists them:
#   {"files": [{"path": "/var/lib/milo/settings.json", "copy": "00-settings.json"},
#              {"path": "/var/lib/milo/podcast_data.json", "copy": null}, ...]}
# `copy` is a file name inside the snapshot directory; null means the file did
# not exist when the update began, so a restore removes it (the new release may
# have created it in a schema the previous one refuses). No manifest means no
# complete snapshot: nothing is restored from the directory.
DATA_MANIFEST_NAME = "manifest.json"
KEY_DATA_FILES = "files"
KEY_DATA_PATH = "path"
KEY_DATA_COPY = "copy"

# The guard's own record while it puts the previous release back, written before
# its first change: a restore cut by a power loss or by TimeoutStartSec is
# resumed by the next start, with the cause it began with, instead of being
# decided afresh from a tree it has already half moved. A restore cut
# MAX_ATTEMPTS times is given up as rollback_failed, so it never loops.
ROLLING_BACK_NAME = "rolling-back.json"
# The restore's steps, in order. A rollback_failed record's "step" names the
# first one that failed; a rolled_back record's names the journal step the
# update had reached.
RESTORE_STEPS = ("checkout", "clean", "venv", "dist", "data", "system_files")

# The lock files a git command cut by a power loss leaves in `.git/`, which make
# every later fetch or checkout refuse; each side removes them by these names
# alone (plus `refs/**/*.lock`): the guard under an open journal, the updater
# before its fetch, once the tree has been found clean.
GIT_LOCKS = ("index.lock", "HEAD.lock", "packed-refs.lock")

# Written by the arriving backend into a committed journal. How many times it
# began its post-commit phase on it, counted before each run, and where the
# last run was (the phase, and the dependency or room it was installing). A
# phase that never finishes — killed at the same item at every start — is not
# run again past MAX_ATTEMPTS - 1 runs: the journal is closed as committed, with
# a detail naming where it was cut.
KEY_POST_COMMIT_RUNS = "post_commit_runs"
KEY_POST_COMMIT_AT = "post_commit_at"

# Written by a backend that finds a journal its guard should have closed before
# it started (an install that never reached its trial, or one carrying a
# failure): the boot (/proc/sys/kernel/random/boot_id) in which it restarted
# itself so that the guard runs again. Once per boot, so a guard that keeps
# failing is never a restart loop.
KEY_GUARD_RETRY_BOOT = "guard_retry_boot"

# Written into last-update.json by the backend once the rollback banner has
# been closed, on any screen: the kiosk's private profile forgets a dismissal
# kept in the browser at every restart. The guard never writes it, so a record
# it writes — a new outcome — is shown again.
KEY_DISMISSED = "dismissed"

# Written by the arriving backend into a committed journal once its post-commit
# phase installed qobuz-proxy again because qobuz-proxy.lock moved between the
# two commits the journal names (at an unchanged version, which nothing on the
# unit can tell apart from a venv still on the previous lock). A phase cut after
# it and run again leaves the sidecar alone instead of installing it again.
KEY_QOBUZ_RELOCKED = "qobuz_relocked"
