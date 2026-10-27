# backend/tests/architecture/test_reference_docs.py
"""docs/reference/ is what the code says, and only its public part.

The pages are published as the developer reference and read at a release tag, by
integrators who cannot see the code. A page left behind by a change documents an
API the unit no longer serves, so the committed files must equal what
tools/docs/generate_reference.py writes now. Fix a failure by running it:

    python tools/docs/generate_reference.py
"""
import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
GENERATOR = REPO / "tools" / "docs" / "generate_reference.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_reference", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN = _load_generator()
# Built at collection, like every other test that reads the app's schema: the
# first import of backend.main touches the appliance's data directories.
PAGES = GEN.render_all(GEN.app_openapi())


@pytest.mark.parametrize("name", sorted(PAGES))
def test_the_committed_page_is_what_the_code_says(name):
    committed = GEN.OUT_DIR / name
    assert committed.exists(), f"docs/reference/{name} is missing — run {GENERATOR.name}"
    assert committed.read_text() == PAGES[name], (
        f"docs/reference/{name} is stale — run python tools/docs/generate_reference.py"
    )


def test_every_page_has_content():
    """A broken extractor writes a page with a header and nothing under it."""
    for name, text in PAGES.items():
        assert text.count("\n|") > 20, name


# What only makes sense inside the repo: a consumer named by its file or store,
# a companion app (the stability tier says who relies on what), a measurement
# log or an incident number. The first paragraph of a docstring is what the
# generator publishes; the rest of it is where such notes belong.
_INTERNAL = re.compile(
    r"\.vue\b|\b[a-z]\w*Store\b|\bZod\b|Milo-(Mac|iOS)|\bmeasured\b"
    r"|\b20\d\d-\d\d-\d\d\b|\bE\d\d\b|\(docs:|\bhandle[A-Z]\w*"
)


def test_no_published_paragraph_carries_an_internal_note():
    leaks = []
    for name, text in PAGES.items():
        for line in text.replace(GEN.STABILITY_NOTE, "").splitlines():
            if _INTERNAL.search(line):
                leaks.append(f"{name}: {line[:120]}")
    assert leaks == []
