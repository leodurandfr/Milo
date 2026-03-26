# Shell Scripts Audit Report

> Generated 2026-03-26 — Manual shellcheck-style analysis (shellcheck not installed on system)

---

## Summary

| Severity | Count | Description |
|----------|-------|-------------|
| **High** | 4 | Silent failure paths, dead flags, third-party script execution |
| **Medium** | 9 | Missing cleanup traps, fragile sed patterns, variable masking |
| **Low** | 18 | SC2086, SC2155, SC2034, minor quoting issues |
| **Info** | 5 | Style inconsistencies, dead code in build chroot |

---

## install.sh

### HIGH

- **L20, 94, 287, 1013**: `REBOOT_REQUIRED` flag is set but **never read** — script always reboots unconditionally at L1185. Dead code (SC2034).
- **L1084**: Third-party `./install.sh` from Waveshare `Brightness.zip` runs without `sudo` and can `exit` the parent installer. No checksum verification on the download.

### MEDIUM

- **No `trap` for cleanup** — Interrupted installs leave temp directories behind. Affects L209, L253, L289, L353, L560, L1077.
- **L265, 419-420, 584**: Version check commands (`roc-recv --version`, `snapserver --version`, etc.) abort the entire script under `set -e` if the binary is not in `$PATH`.
- **L431-432**: `sed` patterns for journald config (`^#RuntimeMaxUse=$`) are too strict — silently fail if the default has trailing whitespace or a value.
- **L648-654**: `configure_fan_control` does not handle the case where neither `/boot/firmware/config.txt` nor `/boot/config.txt` exists — `grep -q` will abort under `set -e`.

### LOW

- **L29, 34, 38, 41**: SC2086 — Unquoted `$1` in all `log_*` functions.
- **L71, 88, 209, 253, 289, 353, 534, 560, 923, 1077, 1134**: SC2155 — `local var=$(cmd)` masks return codes. If `mktemp -d` fails, the empty path is used in subsequent `cd` and `rm -rf`.
- **L880**: `for cursor_file in /usr/share/icons/Adwaita/cursors/*` — glob expands to literal string if directory missing (missing `shopt -s nullglob`).
- **L958**: `echo | while read` pipe runs loop in subshell — variable assignments inside would be lost.
- **L166**: Commented-out `git clone` — dead code.

---

## install/airplay.sh

### MEDIUM

- **L38, 72**: `cd ~` inside sourced functions changes the working directory of the parent `install.sh`, potentially breaking subsequent operations.
- **L122**: D-Bus policy hardcodes `user="milo"` instead of using `$MILO_USER`.
- **L82-84**: FIFO created in `/tmp` at install time will not survive reboot (tmpfs). Runtime code must recreate it.

### LOW

- **L25, 55**: SC2155 — `local temp_dir=$(mktemp -d)`.
- **L32, 69**: SC2086 — `make -j$(nproc)` missing quotes around `$(nproc)`.
- **L26-39**: No trap for temp directory cleanup if `git clone` fails.

---

## install/boot-common.sh

No significant issues. File only defines variables.

- **L16-17**: `BOOT_PARAMS_SCREEN=""` and `CONFIG_PARAMS_SCREEN=""` serve as defaults but are always immediately overridden by the screen scripts.

---

## install/screen-waveshare-7-usb.sh

No significant issues.

- **L15**: `hdmi_cvt=1024 600 60 6 0 0 0` contains spaces — works correctly with `while read -r` processing but would break with word-splitting in other contexts.

---

## install/screen-waveshare-8-dsi.sh

No issues found.

---

## rootfs/usr/local/bin/milo-wait-ready.sh

### MEDIUM

- **L98**: "All services are ready!" logged even when both `wait_for_service` calls failed/timed out. False positive in logs.

### LOW

- **L20-38**: ANSI escape codes piped to `systemd-cat` pollute the journal with raw escape sequences.
- **L55**: Depends on `bc` which may not be installed on a minimal image.
- **L48, 55, 58**: SC2155 — declare and assign separately.

---

## rootfs/usr/local/bin/milo-apply-hardware

### MEDIUM

- **L116-123**: `sed` replacement `s/dtoverlay=vc4-fkms-v3d.*/dtoverlay=vc4-fkms-v3d,audio=off/` destroys any existing comma-separated options on the overlay line.
- **L85**: Backup files in `/boot/firmware/` accumulate without cleanup — can fill the small boot partition.

### LOW

- **L104**: SC2086 — Unquoted `$VALID_OVERLAYS` in `for` loop (intentional word splitting).
- **L118**: `grep "vc4-kms-v3d"` false-matches `vc4-fkms-v3d` lines (sed pattern is more precise, so no runtime bug, but grep fires unnecessarily).
- **L93**: `remove_block` sed fails silently if managed-block markers have trailing whitespace.
- **L186**: No `--dry-run` or `--no-reboot` option — always reboots, even if config unchanged.

---

## rootfs/usr/local/bin/milo-deploy-update

### MEDIUM

- **L99**: `find . -type f` without `-print0` — breaks on filenames with spaces/newlines (SC2038).

### LOW

- **L62**: SC2164 — `cd` without `|| exit`.
- **L161**: `dpkg -i ... || true` silently swallows all errors, not just missing-dep errors.

---

## rootfs/usr/local/bin/milo-set-wifi-country

No significant issues.

- **L33**: Regex `^[A-Z0-9]{2}$` allows digits but comment/error says "two uppercase letters" — documentation inconsistency.

---

## rootfs/usr/local/bin/milo-first-boot

### MEDIUM

- **L33, 47**: Glob pattern `end*` for ethernet interfaces is unusual — likely should be `en*` to match predictable names (`enp*`, `ens*`, `eno*`). Currently misses most non-`ethN` interfaces.
- **L127**: Inconsistent `chown` group — `camilladsp/` gets group `milo-client` while parent `/var/lib/milo-client/` gets group `audio`.

### LOW

- **L33-34**: Missing `shopt -s nullglob` — unexpanded globs iterate once with literal string.

---

## rootfs/usr/local/bin/milo-brightness-7

Python script — shellcheck N/A. No significant issues.

---

## rootfs/usr/local/bin/milo-mdns-probe

Python script — shellcheck N/A. No significant issues.

---

## milo-client/install-client.sh

### HIGH

- **L411, 414, 418**: Variables expanded by outer shell in `bash -c` strings — `$MILO_CLIENT_VENV_DIR` breaks if path contains spaces.

### MEDIUM

- **L149, 250, 298**: SC2155 — `local var=$(cmd)` masks command failure. If `mktemp -d` fails, empty path used in `cd` and `rm -rf`.
- **L228-229**: `sed` patterns for journald only match exact commented-out defaults — silently no-op if format differs.

### LOW

- **L33, 36, 39, 45**: SC2086 — Unquoted `$1` in log functions.
- **L251, 273, 299, 344**: SC2164 — `cd` without `|| exit`.
- **L283**: SC2034 — `DEBIAN_VERSION` not declared `local`, leaks into global scope.

---

## milo-client/rootfs/usr/local/bin/milo-client-apply-hardware

### MEDIUM

- **L119-124**: Same `sed` overlay issue as `milo-apply-hardware` — destroys existing comma-separated options.

### LOW

- **L89**: SC2012 — `ls -1t` to iterate over backup files; should use `find`.
- **L108**: SC2086 — Unquoted `$VALID_OVERLAYS` in `for` loop.

---

## milo-client/rootfs/usr/local/bin/milo-client-install-snapclient

### LOW

- **L8, 13, 36**: SC2292 — Uses `[ ]` instead of `[[ ]]` in a bash script.

---

## milo-client/rootfs/usr/local/bin/milo-client-deploy-update

### HIGH

- **L48**: Pipe subshell (`find | while read`) prevents `set -e` from catching failures inside the file deployment loop — failed copies are **silently ignored**.
- **L37**: Avahi override deployed as `override.conf` but install script uses `milo-override.conf` — creates **duplicate systemd drop-in** with potentially conflicting directives.

### LOW

- **L8, 13, 23, 24, 29, 35**: SC2292 — Uses `[ ]` instead of `[[ ]]`.
- **L48**: SC2044 — `find | while read` without `-print0`.
- **L59**: SC2155 — `target_dir=$(dirname ...)` inside loop.

---

## pi-gen/stage-milo/prerun.sh

No issues found.

---

## pi-gen/stage-milo/00-install-deps/01-run.sh

No issues found.

---

## pi-gen/stage-milo/01-install-audio/00-run.sh

### LOW

- **L33-64**: Mixed host/chroot variable expansion in Snapcast block — correct but fragile to maintain.

---

## pi-gen/stage-milo/01-install-audio/01-run.sh

### LOW

- **L17, 29, 51, 72**: SC2086 — Unquoted version variables in `git clone --branch`.
- **L65-66**: Dead code — `systemctl stop` in build chroot does nothing (systemd not running).

---

## pi-gen/stage-milo/02-install-milo/00-run.sh

### MEDIUM

- **L12**: SC2086 — Unquoted `${MILO_BRANCH}` in `git clone --branch`. User-supplied variable could cause word splitting.

---

## pi-gen/stage-milo/02-install-milo/01-run.sh

### LOW

- **L4-5**: SC2034 — Unused variables `MILO_APP_DIR` and `MILO_DATA_DIR` (all paths hardcoded in heredocs).

---

## pi-gen/stage-milo/03-configure/00-run.sh

### MEDIUM

- **L192-199**: External `wget` from Waveshare with no checksum verification — runs third-party `install.sh`. Supply chain risk.

### LOW

- **L5**: SC2034 — Unused variable `MILO_APP_DIR`.
- **L99-100**: `sed` patterns for journald may not match on different base images.
- **L213**: Dead code — `systemctl stop lightdm` in build chroot.
- **L51**: Hardcoded WiFi regulatory domain `FR`.

---

## pi-gen/stage-milo/03-configure/01-run.sh

### LOW

- **L36-40**: Duplicated `systemctl disable` commands — same disables already done in `01-install-audio/` scripts.

---

## Rootfs Config Files

### MEDIUM

- **`rootfs/home/milo/.bash_profile`** + **`rootfs/home/milo/.config/milo-cage-start.sh`**: Dead kiosk launch path. `getty@tty1` is masked, `milo-kiosk.service` launches Cage directly. `milo-cage-start.sh` has stale Chromium flags diverging from the active service. Both files are still deployed on every update.

### No issues found in:

- `rootfs/etc/asound.conf` — All ALSA devices match AudioSource enum and services
- `rootfs/etc/NetworkManager/dispatcher.d/90-milo-network`
- `rootfs/etc/NetworkManager/dnsmasq-shared.d/milo-captive.conf`
- `rootfs/etc/polkit-1/rules.d/50-milo-networkmanager.rules`
- `rootfs/etc/udev/rules.d/99-milo-screen.rules`
- `rootfs/etc/udev/rules.d/90-milo-cd.rules`
- `rootfs/etc/machine-info`
- `rootfs/usr/share/plymouth/themes/milo/`
- `rootfs/var/lib/milo/camilladsp/config.yml`
- `milo-client/rootfs/etc/asound.conf`
- `milo-client/rootfs/etc/sudoers.d/milo-client`
- `milo-client/rootfs/etc/NetworkManager/dispatcher.d/90-milo-network`
- `milo-client/rootfs/etc/modprobe.d/milo-client-loopback.conf`
- `milo-client/rootfs/etc/avahi/avahi-daemon.conf`
- `milo-client/rootfs/etc/avahi/avahi-daemon.conf.template`
