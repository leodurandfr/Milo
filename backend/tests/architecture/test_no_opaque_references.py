"""No tracked file cites a label that nothing in the repository defines.

The source architecture was designed in a planning document that is gitignored
and was never committed. Its findings and decisions were numbered (E14 for the
first-track rule, D14 for a connected session that names its track, A3 for a
step of its gallery plan), and some two hundred comments, docstrings, test
names and CLAUDE.md lines cited them as "(E14)", "(E04, E05)", "D14" or
"Review of E27:" — references a reader of this tree can never resolve. Each was
replaced by the sentence it stood for, or dropped where the sentence already
said it. One test helper also pointed at the planning directory by path.

What breaks when this fails: a new comment or test cites such a label, or
points into the planning directory. Write the rule or the measurement the label
stood for instead; a reader of the tree has nothing else to go by.

What is not a label, and is left alone narrowly, each with its reason below:
hardware and hex addresses and UUID parts (told apart by the characters next to
the match), a quoted value (`name="A2"`), the codes of a `noqa` directive, the
ruff rule families the lint configuration names, the CIE illuminant D65, and the
rows a checklist numbers and defines in place. Vendored files and lockfiles are
not this tree's prose and are not read.
"""
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
THIS_FILE = Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()

# A letter of the planning document's three series (Errors measured, Decisions,
# gallery-plan steps) and one to three digits. The character before must not be
# one that makes the match part of a longer token: a word (A2DP's other half,
# KEY_A1, 1E5), a hex or MAC group (`DC:A6:32`, `…-A061-…`, `0xE4`), a dotted
# version or a color (`#E4E4E4`). The same on the right, where a colon before a
# hex digit is a MAC group (`D8:3A:DD`) and one before a space ends a phrase
# ("E55: shairport announces…").
_LABEL = re.compile(r"(?<![A-Za-z0-9_.:#-])([ADE]\d{1,3})(?![A-Za-z0-9_]|:[0-9A-Fa-f])")
_PLANS_PATH = re.compile(r"docs/plans\b")
# A `# noqa: E402 -- reason` names ruff rules, not labels.
_NOQA = re.compile(r"noqa:\s*[A-Z0-9]+(?:\s*,\s*[A-Z0-9]+)*")
_QUOTES = "\"'`"

# The CIE standard illuminant the screen's color correction is measured against.
NOT_A_LABEL = {"D65"}

# Files that name ruff's rule families and codes in prose, to say which are
# selected and why the rest are not (the lint floor's own description).
LINT_CONFIG_PROSE = {"CLAUDE.md", "pyproject.toml"}
RUFF_CODES = {"E4", "E7", "E9", "E402", "E731"}

# A checklist that numbers its own rows and defines each one in the table it
# sits in: "A2" there is the row two lines up, not a pointer elsewhere.
SELF_NUMBERED = {"docs/manual/physical-verification.md": {"A", "D"}}

# The one place the planning directory may be named: the line that keeps it
# out of git.
PLANS_PATH_ALLOWED = {".gitignore"}

# Upstream's Swift, edited only by a snapshot refresh, and generated lockfiles.
_NOT_READ_PREFIXES = ("backend/tests/contracts/vendor/",)
_NOT_READ_NAMES = {"package-lock.json"}
_NOT_READ_SUFFIXES = (".lock",)


def references_in(name, text):
    """Every opaque reference in one file's text, as `(path:line, label)`."""
    found = []
    letters_defined_here = SELF_NUMBERED.get(name, set())
    for number, line in enumerate(text.splitlines(), start=1):
        where = f"{name}:{number}"
        if _PLANS_PATH.search(line) and name not in PLANS_PATH_ALLOWED:
            found.append((where, "docs/plans"))
        scanned = _NOQA.sub("", line)
        for match in _LABEL.finditer(scanned):
            label = match.group(1)
            before = scanned[match.start() - 1] if match.start() > 0 else ""
            after = scanned[match.end()] if match.end() < len(scanned) else ""
            if before and before in _QUOTES and after == before:
                continue  # a quoted value: name="A2", 'E1'
            if label in NOT_A_LABEL or label[0] in letters_defined_here:
                continue
            if name in LINT_CONFIG_PROSE and label in RUFF_CODES:
                continue
            found.append((where, label))
    return found


def _tracked_text_files():
    names = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True,
    ).stdout.decode().split("\0")
    for name in filter(None, names):
        if name == THIS_FILE or name.startswith(_NOT_READ_PREFIXES):
            continue
        if Path(name).name in _NOT_READ_NAMES or name.endswith(_NOT_READ_SUFFIXES):
            continue
        try:
            yield name, (REPO_ROOT / name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue


def test_a_simulated_reference_is_found():
    """The scanner finds each shape a label was cited in, and none of the data
    that looks like one. Fails when the pattern stops matching a real citation
    (the tree scan below would then pass on anything) or starts matching data."""
    text = "\n".join([
        "# the drive moves on what udev announces (E37).",
        '    """E55: shairport announces the session first."""',
        "# measured twice (E04, E05, E48, E49)",
        "// so every primitive reads in both (A2/A3)",
        "// a connected session keeps the player (D14)",
        "# see docs/plans/source-architecture.md",
        # Data that must not be read as a label:
        'mac = "D8:3A:DD:12:34:56"; other = "DC:A6:32:7E:D3:43"',
        "uuid = 0000110B-0000-1000-8000-00805F9B34FB-A061",
        'await registry.update_client(mac, name="A2")',
        "import x  # noqa: E402, A002 -- grouped with its block",
        "profile A2DP; key KEY_A1; gain 1E5; color #E4E4E4; v1.E12",
        "the panel's white is corrected to D65",
    ])
    found = references_in("backend/example.py", text)
    assert found == [
        ("backend/example.py:1", "E37"),
        ("backend/example.py:2", "E55"),
        ("backend/example.py:3", "E04"),
        ("backend/example.py:3", "E05"),
        ("backend/example.py:3", "E48"),
        ("backend/example.py:3", "E49"),
        ("backend/example.py:4", "A2"),
        ("backend/example.py:4", "A3"),
        ("backend/example.py:5", "D14"),
        ("backend/example.py:6", "docs/plans"),
    ]
    # The lint prose and a self-numbered checklist keep their own names, and
    # nowhere else does.
    assert references_in("CLAUDE.md", "E4/E7/E9 stay out; E402 and E731 too") == []
    assert references_in("docs/manual/physical-verification.md", "| A2 | Radio |\n| D3 | Keys |") == []
    assert references_in("docs/manual/physical-verification.md", "see (E14)") == [
        ("docs/manual/physical-verification.md:1", "E14"),
    ]
    assert references_in(".gitignore", "docs/plans/") == []


def test_no_tracked_file_cites_an_undefined_label():
    """Every tracked text file is free of planning-document labels and of the
    planning directory's path. Fails with `path:line label` for each one."""
    files = dict(_tracked_text_files())
    # A broken listing or reader must fail here, not pass on an empty tree.
    assert len(files) > 1000, f"only {len(files)} tracked text files read"
    for must_be_read in ("CLAUDE.md", "docs/architecture.md"):
        assert must_be_read in files, f"{must_be_read} was not read"
    for tree in ("backend/", "frontend/src/", "frontend/tests/", "milo-client/"):
        assert any(name.startswith(tree) for name in files), f"nothing under {tree} was read"

    found = [hit for name, text in files.items() for hit in references_in(name, text)]
    assert not found, "opaque references to the planning document:\n" + "\n".join(
        f"  {where}  {label}" for where, label in found
    )
