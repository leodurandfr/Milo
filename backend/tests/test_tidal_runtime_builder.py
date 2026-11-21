"""provisioning/tidal_connect_runtime.py checks every byte it unpacks.

It runs as root on the unit now (`milo-deploy-update install-tidal-runtime`,
the first time Tidal is selected), so what it installs must be what the file
pins, whatever a registry or a mirror serves: the manifest against
IMAGE_DIGEST, each layer against the digest the manifest names, each bullseye
package against BULLSEYE_DEBS — read from snapshot.debian.org, since bullseye
left LTS and leaves deb.debian.org with it.

If these fail, a unit can unpack as root a layer or a library nobody pinned,
or a package the overlay needs is fetched with no digest at all.
"""
import hashlib
import importlib.util
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILDER = REPO_ROOT / "provisioning" / "tidal_connect_runtime.py"


@pytest.fixture(scope="module")
def builder():
    spec = importlib.util.spec_from_file_location("tidal_connect_runtime", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_download_that_does_not_match_its_digest_is_refused_and_removed(builder, tmp_path):
    served = tmp_path / "layer.tar.gz"
    served.write_bytes(b"what the registry served")
    kept = tmp_path / "kept"

    builder._fetch(served.as_uri(), str(kept), hashlib.sha256(served.read_bytes()).hexdigest(),
                   "layer 1", timeout=5)
    assert kept.read_bytes() == served.read_bytes()

    refused = tmp_path / "refused"
    with pytest.raises(SystemExit, match="layer 2"):
        builder._fetch(served.as_uri(), str(refused), "0" * 64, "layer 2", timeout=5)
    assert not refused.exists()


def test_a_manifest_is_refused_unless_its_bytes_are_the_pinned_digest(builder):
    manifest = b'{"schemaVersion": 2, "layers": []}'
    builder._check_digest(manifest, "sha256:" + hashlib.sha256(manifest).hexdigest(), "manifest")
    with pytest.raises(SystemExit, match="manifest"):
        builder._check_digest(manifest + b" ", "sha256:" + hashlib.sha256(manifest).hexdigest(),
                              "manifest")


def test_every_package_the_overlay_takes_is_pinned_and_every_pin_is_taken(builder):
    """The overlay and the rate plugin are the two readers of BULLSEYE_DEBS: a
    package added to either without a pin stops the build at its download, and
    a pin nothing reads is a digest nobody checks the day it moves."""
    wanted = set(builder.OVERLAY_PACKAGES) | {builder.PLUGIN_PACKAGE}
    assert len(wanted) >= 5, wanted
    assert set(builder.BULLSEYE_DEBS) == wanted

    for package, (pool_path, sha256) in builder.BULLSEYE_DEBS.items():
        assert re.fullmatch(rf"pool/main/[^/]+/[^/]+/{re.escape(package)}_[^/]+_armhf\.deb", pool_path), pool_path
        assert re.fullmatch(r"[0-9a-f]{64}", sha256), (package, sha256)
    # A moment, not a mirror: what the snapshot serves there never moves.
    assert re.fullmatch(r"https://snapshot\.debian\.org/archive/debian/\d{8}T\d{6}Z",
                        builder.DEBIAN_SNAPSHOT), builder.DEBIAN_SNAPSHOT


def test_a_manifest_the_registry_altered_is_refused(builder, monkeypatch):
    """`_manifest` checks what it read, not only what `_check_digest` is given."""
    pinned = b'{"schemaVersion": 2, "layers": []}'
    digest = "sha256:" + hashlib.sha256(pinned).hexdigest()

    class Served:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self.body

    monkeypatch.setattr(builder.urllib.request, "urlopen", lambda *a, **k: Served(pinned))
    assert builder._manifest("repo", digest, "token") == {"schemaVersion": 2, "layers": []}

    altered = b'{"schemaVersion": 2, "layers": [{"digest": "sha256:00"}]}'
    monkeypatch.setattr(builder.urllib.request, "urlopen", lambda *a, **k: Served(altered))
    with pytest.raises(SystemExit, match="manifest"):
        builder._manifest("repo", digest, "token")


def test_a_package_the_snapshot_serves_altered_is_refused_and_removed(builder, monkeypatch,
                                                                     tmp_path):
    """`_download_deb` hands its pin to the download: a package whose bytes are
    not the pinned ones never reaches dpkg-deb."""
    package = builder.PLUGIN_PACKAGE
    pool_path, _ = builder.BULLSEYE_DEBS[package]
    mirror = tmp_path / "snapshot"
    served = mirror / pool_path
    served.parent.mkdir(parents=True)
    served.write_bytes(b"not the package the pin names")
    monkeypatch.setattr(builder, "DEBIAN_SNAPSHOT", mirror.as_uri())
    into = tmp_path / "debs"
    into.mkdir()

    with pytest.raises(SystemExit, match=re.escape(Path(pool_path).name)):
        builder._download_deb(package, str(into))
    assert list(into.iterdir()) == []


def test_a_package_with_no_pin_is_never_fetched(builder, tmp_path):
    with pytest.raises(SystemExit, match="no pinned"):
        builder._download_deb("libpulse0", str(tmp_path))
