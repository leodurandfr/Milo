"""Structural guardrail: the four compiled dependencies are built the same way on both paths.

`nqptp`, `shairport-sync`, `bluez-alsa` and `roc-toolkit` are compiled from
source, and the two provisioning paths each carry their own copy of the recipe:
`install/{airplay,bluez-alsa,roc-toolkit}.sh` for a script install, and
`pi-gen/stage-milo/01-install-audio/01-run.sh` for the image.

That duplication is structural rather than sloppy. Every other pi-gen block
`source`s the `install/` module instead of restating it, but `01-install-audio`
runs *before* `02-install-milo` clones the repo — there is nothing to source
yet. It is the same constraint that makes `dependencies.env` travel as a sibling
copied in by the builder.

So the recipes cannot be deduplicated, only compared — and nothing compared
them. A build flag is not a detail here: `--with-metadata` and
`--with-metadata-pipe` are what make shairport-sync emit AirPlay title, artist
and cover at all, and `shairport-5x-metadata-regression` records that "audio
plays" proves nothing about them. Dropping one on a single path would ship an
image and a script install that behave differently, with no error anywhere and
nothing to compare on the appliance but the absence of metadata.

Compared as *sets of flags per invocation*, not as text: the two sides format
them differently (line continuations, indentation, a leading `sudo`), and none
of that is the contract. The contract is which flags each build gets.

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing on
an empty surface.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# The two sides. `install/` spreads its recipes over three modules; the image
# keeps all of them in one stage script.
SCRIPT_MODULES = [
    REPO_ROOT / "install" / "airplay.sh",
    REPO_ROOT / "install" / "bluez-alsa.sh",
    REPO_ROOT / "install" / "roc-toolkit.sh",
]
IMAGE_STAGE = REPO_ROOT / "pi-gen" / "stage-milo" / "01-install-audio" / "01-run.sh"

# A build invocation in *command position* — start of a statement, or after a
# separator. `scons` is also an apt package name in install/roc-toolkit.sh, and
# matching it anywhere on a line counted that dependency list as a build recipe.
STATEMENT_BREAK = re.compile(r"\|\||&&|[|;&()]")
BUILD_RE = re.compile(r"^\s*(?:sudo\s+)?(\.{1,2}/configure|scons)\b(.*)$")

# `MILO_USER="${MILO_USER:-milo}"` — the install modules parameterise what pi-gen
# writes literally. The two sides must be compared on values, not spellings.
ASSIGN_RE = re.compile(r"^\s*([A-Z_][A-Z0-9_]*)=\"?\$\{\1:-([^}\"]*)\}\"?", re.MULTILINE)


def _statements(path: Path) -> str:
    """The file with backslash continuations joined and comments dropped."""
    text = re.sub(r"\\\n\s*", " ", path.read_text(encoding="utf-8"))
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _resolve(word: str, defaults: dict[str, str]) -> str:
    for name, value in defaults.items():
        word = word.replace(f'"${name}"', value).replace(f"${{{name}}}", value)
        word = word.replace(f"${name}", value)
    return word


def _recipes(paths: list[Path]) -> set[tuple[str, frozenset[str]]]:
    """(tool, flags) for every build invocation, as an order-insensitive set.

    Non-flag words are dropped — `install` as a scons target is a word, not a
    flag, and keeping it would make two identical builds compare unequal for a
    reason nobody cares about. What is compared is the `--…` set, with the
    install modules' own defaults substituted in.
    """
    defaults = {}
    for path in paths:
        defaults.update(dict(ASSIGN_RE.findall(path.read_text(encoding="utf-8"))))

    found = set()
    for path in paths:
        for line in _statements(path).splitlines():
            for fragment in STATEMENT_BREAK.split(line):
                match = BUILD_RE.match(fragment)
                if not match:
                    continue
                tool, tail = match.groups()
                flags = frozenset(
                    _resolve(w, defaults) for w in tail.split() if w.startswith("--")
                )
                found.add((tool.lstrip("."), flags))
    return found


def test_the_extractor_sees_both_sides():
    """A side that reads as empty would make the comparison below vacuous."""
    script = _recipes(SCRIPT_MODULES)
    image = _recipes([IMAGE_STAGE])

    assert len(script) >= 3, f"only {len(script)} build recipes read from install/"
    assert len(image) >= 3, f"only {len(image)} build recipes read from the pi-gen stage"

    # The shairport-sync recipe is the one that matters most and the one with the
    # most flags; if the parser truncated a continued line, it would not be here.
    biggest = max(len(flags) for _, flags in script)
    assert biggest >= 8, f"the largest flag set read from install/ has only {biggest} flags"
    assert any(
        {"--with-metadata", "--with-metadata-pipe", "--with-airplay-2"} <= set(flags)
        for _, flags in script
    ), "the shairport-sync flags were not extracted; the parser is broken"

    # The variable resolver must be exercised, or a flag written `$MILO_USER` on
    # one side and `milo` on the other would read as drift for a reason that is
    # only spelling.
    assert any("--with-bluealsauser=milo" in flags for _, flags in script), (
        "no install/ flag resolved through MILO_USER; the resolver is dead"
    )
    # ...and `scons` as an apt package name must not read as a build recipe.
    assert all(flags for _, flags in script), (
        f"a flagless build recipe was extracted: {sorted(script)}"
    )


def test_both_paths_build_the_dependencies_with_the_same_flags():
    """A flag on one path only ships two appliances that behave differently.

    The recipes cannot be shared — `01-install-audio` runs before the repo is
    cloned — so this is the only thing standing between them.
    """
    script = _recipes(SCRIPT_MODULES)
    image = _recipes([IMAGE_STAGE])

    def render(recipes):
        return "\n".join(
            f"  {tool} {' '.join(sorted(flags))}" for tool, flags in sorted(recipes)
        )

    assert script == image, (
        "the two provisioning paths compile the dependencies differently.\n"
        f"only in install/:\n{render(script - image) or '  (none)'}\n"
        f"only in pi-gen/:\n{render(image - script) or '  (none)'}"
    )
