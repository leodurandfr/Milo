# backend/core/updates/dependency_versions.py
"""
Reads the validated dependency set out of the repo's `dependencies.env`.

That file is the single declaration of every dependency version Milō ships, and
of the sha256 of every file those versions download — the install scripts, the
pi-gen stage and this module all read it, and none of them restates a value.
Here it becomes each catalog entry's `"validated_version"`, which `VersionService`
pins the offered release to, and its `"validated_sha256"`, which `UpdateService`
checks each download against and hands to the root helper that checks it again.

Deliberately a `KEY=value` file rather than JSON or a Python module: the two
install trees must source it in bash before apt has run, on an OS that ships no
`jq`, and pi-gen builds from a copy that cannot import anything from `backend/`.

Fails loud on a missing file, an unparseable one, or a line that is neither a
version nor a digest: a silently empty set would un-pin every dependency at once
and put the "latest upstream release" button back, which is the exact thing the
manifest exists to remove — and a digest misread is an install refused, or
worse, one never checked.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPO_ROOT / "dependencies.env"

# `KEY=value` at line start. Values carry no spaces, no quotes and no
# expansions, so anything else is a shape this parser must not guess at.
_ENTRY_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(\S+)$", re.MULTILINE)

# The two kinds of line, by suffix, and the one shape each value may take.
VERSION_SUFFIX = "_VERSION"
DIGEST_SUFFIX = "_SHA256"
_SHAPES = {
    VERSION_SUFFIX: re.compile(r"\d+(\.\d+)+"),
    DIGEST_SUFFIX: re.compile(r"[0-9a-f]{64}"),
}


def load_dependency_manifest(path: Path | None = None) -> dict[str, str]:
    """Parse `dependencies.env` into {SHELL_VAR_NAME: value}, versions and digests."""
    path = path if path is not None else MANIFEST_PATH
    if not path.is_file():
        raise FileNotFoundError(f"dependency manifest missing: {path}")

    text = "\n".join(
        line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")
    )
    entries = dict(_ENTRY_RE.findall(text))
    if not any(key.endswith(VERSION_SUFFIX) for key in entries):
        raise ValueError(f"dependency manifest declares no version: {path}")
    for key, value in entries.items():
        shape = next((s for suffix, s in _SHAPES.items() if key.endswith(suffix)), None)
        if shape is None:
            raise ValueError(f"{path}: {key} is neither a *{VERSION_SUFFIX} nor a *{DIGEST_SUFFIX} line")
        if not shape.fullmatch(value):
            raise ValueError(f"{path}: {key}={value!r} is not the shape a {key.rsplit('_', 1)[1]} line takes")
    return entries


def apply_validated_versions(programs: dict, manifest: dict[str, str] | None = None) -> dict:
    """Fill each program's `validated_version` from its `validated_version_key`,
    and its `validated_sha256` ({asset: digest}) from its `validated_sha256_keys`.

    `catalog.py` calls it once, at import. A Milō update reconciles the set only
    once the new release has started and held (`UpdateService.run_post_commit`),
    so the manifest that process imported is already the new release's own.

    A key the manifest does not declare raises `KeyError`: a dependency whose
    version silently vanished would fall back to "whatever GitHub's
    releases/latest returns", which is the button the manifest exists to remove,
    and an asset whose digest vanished would install unchecked.
    """
    resolved = manifest if manifest is not None else load_dependency_manifest()
    for config in programs.values():
        # shairport-sync's NQPTP is pinned like a program but installed with it.
        for entry in (config, config.get("nqptp") or {}):
            key = entry.get("validated_version_key")
            if key:
                entry["validated_version"] = resolved[key]
            digest_keys = entry.get("validated_sha256_keys")
            if digest_keys:
                entry["validated_sha256"] = {asset: resolved[k] for asset, k in digest_keys.items()}
    return programs
