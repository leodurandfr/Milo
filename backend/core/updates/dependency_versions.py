# backend/core/updates/dependency_versions.py
"""
Reads the validated dependency set out of the repo's `dependencies.env`.

That file is the single declaration of every dependency version Milō ships —
the install scripts, the pi-gen stage and this module all read it, and none of
them restates a number. Here it becomes each catalog entry's
`"validated_version"`, which `VersionService` pins the offered release to.

Deliberately a `KEY=value` file rather than JSON or a Python module: the two
install trees must source it in bash before apt has run, on an OS that ships no
`jq`, and pi-gen builds from a copy that cannot import anything from `backend/`.

Fails loud on a missing file or an unparseable one: a silently empty set would
un-pin every dependency at once and put the "latest upstream release" button
back, which is the exact thing the manifest exists to remove.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPO_ROOT / "dependencies.env"

# `KEY=value` at line start. Values are bare versions — no spaces, no quotes,
# no expansions — so anything else is a shape this parser must not guess at.
_ENTRY_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(\S+)$", re.MULTILINE)


def load_dependency_versions(path: Path | None = None) -> dict[str, str]:
    """Parse `dependencies.env` into {SHELL_VAR_NAME: version}.

    Resolved at call time, not bound at import: `UpdateService` re-reads the
    file after a `git pull` has replaced it under the running process.
    """
    path = path if path is not None else MANIFEST_PATH
    if not path.is_file():
        raise FileNotFoundError(f"dependency manifest missing: {path}")

    text = "\n".join(
        line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")
    )
    versions = dict(_ENTRY_RE.findall(text))
    if not versions:
        raise ValueError(f"dependency manifest declares no version: {path}")
    return versions


def apply_validated_versions(programs: dict, versions: dict[str, str] | None = None) -> dict:
    """Fill each program's `validated_version` from its `validated_version_key`.

    Two callers, and the second is the reason the association and the number are
    separate keys. `catalog.py` calls it once at import; `UpdateService` calls it
    again *after* a `git pull`, because the pulled tree carries a new manifest
    that the running process imported minutes ago and cannot see. The key is a
    constant a pull never changes, so re-reading the file is enough — reloading
    the module would not be.

    A key the manifest does not declare raises `KeyError`: a dependency whose
    version silently vanished would fall back to "whatever GitHub's
    releases/latest returns", which is the button the manifest exists to remove.
    """
    resolved = versions if versions is not None else load_dependency_versions()
    for config in programs.values():
        key = config.get("validated_version_key")
        if key:
            config["validated_version"] = resolved[key]
    return programs
