# Third-party notices

Milō's own code is licensed under the PolyForm Shield License 1.0.0 ([LICENSE](LICENSE)). A
Milō image, and a unit in service, also run programs other people wrote, each under its own
license, which Milō's license does not change. This file names them: what each one is, the
version this release carries, its license and where its source lives. It ends with the written
offer for the source code of the components whose license calls for one.

Licenses are given as [SPDX](https://spdx.org/licenses/) identifiers. **(to confirm)** marks a
point that was not settled from upstream's own sources when this file was written. Upstream's
license files are authoritative over this summary, and the terms of each Debian package are
installed on every unit, at `/usr/share/doc/<package>/copyright`.

A main unit serves this file at `/THIRD_PARTY_NOTICES.md`, and Settings › System › Third-party
licenses opens it; every image carries it at the root of the Milō checkout.

## 1. Programs pinned by the dependency manifest

The image builds or installs each one at the version [dependencies.env](dependencies.env)
declares for this release. The six that Settings › Updates installs are downloaded by the unit
itself, from the same upstream, when it updates them. Milō does not patch their sources;
qobuz-proxy alone runs with five adaptations Milō applies at launch, binding to upstream's names,
from `rootfs/usr/local/bin/milo-qobuz` in this repository.
`backend/tests/architecture/test_third_party_notices.py` holds this table equal to the manifest.

| Program | Version | Manifest line | License | Source | In the image as |
|---|---|---|---|---|---|
| go-librespot (Spotify Connect) | 0.10.7 | `GO_LIBRESPOT_VERSION` | GPL-3.0 (to confirm: `-only` or `-or-later`) | https://github.com/devgianlu/go-librespot/tree/v0.10.7 | upstream's arm64 binary |
| shairport-sync (AirPlay 2) | 5.6.3 | `SHAIRPORT_SYNC_VERSION` | MIT, with BSD-3-Clause and ISC parts (file by file, as upstream's `LICENSES` lists) | https://github.com/mikebrady/shairport-sync/tree/5.6.3 | built from the tag's source archive |
| NQPTP (AirPlay 2 timing) | 1.2.9 | `NQPTP_VERSION` | GPL-2.0-only | https://github.com/mikebrady/nqptp/tree/1.2.9 | built from the tag's source archive |
| Snapcast (snapserver, snapclient) | 0.35.0 | `SNAPCAST_VERSION` | GPL-3.0-or-later | https://github.com/snapcast/snapcast/tree/v0.35.0 | upstream's arm64 Debian packages |
| CamillaDSP | 4.1.3 | `CAMILLADSP_VERSION` | GPL-3.0-only OR MPL-2.0 | https://github.com/HEnquist/camilladsp/tree/v4.1.3 | upstream's aarch64 binary |
| qobuz-proxy (Qobuz Connect) | 1.7.8 | `QOBUZ_PROXY_VERSION` | MIT | https://github.com/leolobato/qobuz-proxy/tree/v1.7.8 | Python package, installed from the tag's source archive (`--no-deps`) over the dependencies [qobuz-proxy.lock](qobuz-proxy.lock) pins by hash, run through `milo-qobuz` |
| Navidrome (Music Library) | 0.64.2 | `NAVIDROME_VERSION` | GPL-3.0 (to confirm: `-only` or `-or-later`) | https://github.com/navidrome/navidrome/tree/v0.64.2 | upstream's arm64 binary |
| BlueALSA (bluez-alsa) | 4.3.1 | `BLUEZ_ALSA_VERSION` | MIT; linked against BlueZ's `libbluetooth` (GPL-2.0-or-later) | https://github.com/arkq/bluez-alsa/tree/v4.3.1 (commit `f11569451b98a765adf9abc081632bb013f89f57`) | built from the tag |
| Roc Toolkit (Mac streaming) | 0.4.0 | `ROC_TOOLKIT_VERSION` | MPL-2.0; linked statically with OpenFEC 1.4.2.9, which is CECILL-C (every OpenFEC source file that carries a CeCILL header names CeCILL-C), with parts under their authors' permissive terms and one file under CC-BY-SA-3.0 | https://github.com/roc-streaming/roc-toolkit/tree/v0.4.0 (commit `62401be9877ee087de49a40c35ae64c75a820387`); OpenFEC: https://github.com/roc-streaming/openfec/tree/v1.4.2.9 | built from the tag; roc's own build (`--build-3rdparty=openfec`) downloads OpenFEC at the version roc's `3rdparty/SConscript` pins and builds it with roc |

## 2. Downloaded by a unit, never carried by an image

- **Tidal Connect.** Tidal's proprietary daemon, with iFi's device certificate, published as the
  container image `edgecrush3r/tidal-connect` and pinned by digest in
  `provisioning/tidal_connect_runtime.py`. It is not free software and not Milō's to
  redistribute, so no image and no release carries it: a unit downloads it, with the Debian 11
  ("bullseye") packages it runs on (from snapshot.debian.org, each pinned by sha256), the first
  time Tidal is selected. Its terms are Tidal's, and there is no source to offer.
- **A version past this release's set.** A program installed from Settings › Updates at the
  version upstream published past `dependencies.env` comes from upstream's release, under the
  license of that release.
- **Multiroom speakers** download snapclient and CamillaDSP from the upstream releases in section
  1, at the version their server names.

## 3. The operating system

The image is Raspberry Pi OS Lite, on Debian 13 ("trixie") for arm64, built with
[pi-gen](https://github.com/RPi-Distro/pi-gen). It carries Debian's and Raspberry Pi's packages
under their own licenses: among them the Linux kernel (GPL-2.0-only), the GNU C library
(LGPL-2.1-or-later), systemd (LGPL-2.1-or-later), GNU coreutils and bash (GPL-3.0-or-later),
Python (PSF-2.0), and Raspberry Pi's boot firmware, which Raspberry Pi Ltd distributes under its
own redistributable license, without source. On a unit, `dpkg-query -W` lists every package and
the version the image carries.

The packages [system-manifest.env](system-manifest.env) adds include these, as Debian packages
them. The test above holds each name to the manifest.

| Package | License | What it does here |
|---|---|---|
| `mpv` | LGPL-2.1-or-later sources; GPL-2.0-or-later as a whole unless built without its GPL parts (to confirm Debian's build) | plays Radio, Podcasts, CD and the Music Library |
| `nginx` | BSD-2-Clause | serves the web interface |
| `chromium` | BSD-3-Clause, with the many components it bundles under their own licenses | the screen's kiosk |
| `cage` | MIT | the kiosk's compositor |
| `seatd` | MIT | the kiosk's seat |
| `plymouth` | GPL-2.0-or-later | the boot splash |
| `bluez` | GPL-2.0-or-later | Bluetooth |
| `avahi-daemon` | LGPL-2.1-or-later | mDNS (`milo.local`) |
| `libasound2-plugins` | LGPL-2.1-or-later | ALSA plugins |
| `libcairo2` | LGPL-2.1-only OR MPL-1.1 | radio logos |
| `libportaudio2` | MIT | qobuz-proxy's audio output |
| `libdiscid0` | LGPL-2.1-or-later | CD identification |
| `ir-keytable` | GPL-2.0 (to confirm: `-only` or `-or-later`) | IR remote |
| `ntfs-3g` | GPL-2.0-or-later | NTFS storage |
| `exfatprogs` | GPL-2.0-or-later | exFAT storage |
| `cifs-utils` | GPL-3.0-or-later | SMB shares |
| `smbclient` | GPL-3.0-or-later | SMB share discovery |
| `nfs-common` | GPL-2.0-or-later | NFS shares |
| `git` | GPL-2.0-only | the release checkout |
| `openssh-client` | BSD-2-Clause and other permissive terms (`SSH-OpenSSH`) | release signature checks |
| `fonts-noto`, `fonts-noto-cjk`, `fonts-noto-color-emoji`, `fonts-lohit-deva` | OFL-1.1 | the kiosk's fonts |

## 4. Python packages

The backend runs in a virtual environment installed from [requirements.lock](requirements.lock),
a multiroom speaker's from `milo-client/app/requirements.lock`, and qobuz-proxy's (section 1)
from [qobuz-proxy.lock](qobuz-proxy.lock); each lock pins the version and the hash of every
package. A Python package is installed as its source, plus whatever compiled extension its wheel
carries. The packages of note, at the version the backend's lock pins:

| Package | Version | License |
|---|---|---|
| fastapi | 0.165.0 | MIT |
| starlette | 1.10.0 | BSD-3-Clause |
| uvicorn | 0.54.0 | BSD-3-Clause |
| pydantic | 2.14.4 | MIT |
| aiohttp | 3.14.5 | Apache-2.0 AND MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| websockets | 17.4 | BSD-3-Clause |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| pyjwt | 2.15.1 | MIT |
| dbus-next | 0.2.3 | MIT |
| evdev | 2.0.0 | BSD-3-Clause |
| pillow | 13.0.0 | MIT-CMU |
| cairosvg | 2.9.1 | LGPL-3.0-or-later |
| discid | 1.4.2 | LGPL-3.0-or-later |
| pyudev | 0.24.5 | LGPL-2.1-or-later |
| shazamio | 0.9.0 | MIT |
| musicbrainzngs | 0.7.1 | BSD-2-Clause, with ISC parts (to confirm) |
| certifi | 2026.11.20 | MPL-2.0 |
| lgpio | 0.2.2.0 | Unlicense (to confirm) |

## 5. The web interface

The built interface (`frontend/dist/`) bundles these npm packages, at the version
[frontend/package-lock.json](frontend/package-lock.json) pins:

| Package | Version | License |
|---|---|---|
| axios | 1.9.0 | MIT |
| pinia | 2.3.1 | MIT |
| vue | 3.5.13 | MIT |
| vue-router | 4.5.1 | MIT |
| zod | 4.3.5 | MIT |

Their own dependencies are listed in the same lock, each with its license; at this release every
one of them is under `MIT`, `ISC`, `BSD-2-Clause` or `BSD-3-Clause`.

It also carries these fonts:

- Space Mono (Colophon Foundry), Noto Sans Devanagari and Noto Sans SC (Google): OFL-1.1.
- Neue Montreal (Pangram Pangram Foundry): a commercial typeface, used under the license the
  project holds for it (to confirm that this license covers its redistribution in the
  interface).

The names and logos of the services Milō plays from are their owners' trademarks.

## 6. Written offer for source code

Some components above are licensed under the GNU General Public License, version 2 or 3 (GPL),
the GNU Lesser General Public License (LGPL), the Mozilla Public License 2.0 (MPL) or the CeCILL
licenses, and a Milō image carries them in object code form. Among them: NQPTP (GPL-2.0-only),
go-librespot, Snapcast, CamillaDSP and Navidrome (GPL-3.0), BlueALSA (linked against the GPL
`libbluetooth`), Roc Toolkit (MPL-2.0, with OpenFEC under CECILL-C), and the GPL and LGPL packages of the
operating system in section 3, the Linux kernel first.

**Where the source already is.**

- Section 1: each tag linked above is the complete source of the version the image carries,
  together, for Roc Toolkit, with the OpenFEC tag its row links, which roc's build downloads.
  The scripts that build and install them, which are part of their corresponding source, are in
  this repository at the release's tag: `pi-gen/stage-milo/` (the build options),
  `provisioning/`, and `dependencies.env` (each version, the sha256 of each archive, and the
  commit each cloned tag named).
- Section 3: each package's source package, at the version the image carries, is in the
  Debian archive ([snapshot.debian.org](https://snapshot.debian.org/)) or Raspberry Pi's
  ([archive.raspberrypi.com](https://archive.raspberrypi.com/debian/)); the kernel's is at
  [github.com/raspberrypi/linux](https://github.com/raspberrypi/linux). On a unit,
  `dpkg-query -W -f='${source:Package} ${source:Version}\n'` lists the source packages and
  versions its image carries.
- Section 4: the packages are installed as source, and each version the lock pins is on
  [PyPI](https://pypi.org/).

**The offer.** For at least three years after the Milō project last distributes a given Milō
image through this repository's releases, it will give any third party who asks a complete
machine-readable copy of the corresponding source code of every component in that image that is
licensed under the GPL, the LGPL, the MPL or the CeCILL, under the terms of that component's
license: as a download at no charge, or on a medium customarily used for software interchange
for a charge no more than the cost of physically performing the distribution. This offer is made
under section 3(b) of the GPL version 2 for the components under that version, NQPTP and the
Linux kernel among them, and under the corresponding terms of the other licenses for the rest.

To ask for it, open an issue in this repository
([github.com/leodurandfr/Milo/issues](https://github.com/leodurandfr/Milo/issues)) titled
"Source request", naming the release tag of the image (Settings › System shows it) and the
components you want; to ask without a public issue, use the private reporting channel that
[SECURITY.md](SECURITY.md) describes and say that it is a source request. The answer comes in the
same thread.
