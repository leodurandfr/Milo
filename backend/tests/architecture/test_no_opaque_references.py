"""No tracked file cites a label that nothing in the repository defines.

The source architecture was designed in a planning document that is gitignored
and was never committed. Its findings and decisions were numbered (E14 for the
first-track rule, D14 for a connected session that names its track, A3 for a
step of its gallery plan), and some two hundred comments, docstrings, test
names and CLAUDE.md lines cited them as "(E14)", "(E04, E05)", "D14" or
"Review of E27:" — references a reader of this tree can never resolve. Each was
replaced by the sentence it stood for, or dropped where the sentence already
said it. One test helper also pointed at the planning directory by path.

The same document was cited two more ways, and other plans one more: by its
sections — `(docs: "le fil", §7)`, `(§2, §5)`, `docs: source architecture,
phase 3a` — and by the step labels of plans that were never committed either
(`P1-6`, `P2-8`, `P1 fix`). Those went the same way, and are refused here too.
A section sign is a reference only when a named document stands before it: an
RFC (`RFC 9110 §10.2.3`), a license (`GPLv2 §3`), or a file of this tree
(`docs/architecture.md § Security`, `CLAUDE.md § Language`, a Markdown link to
one). A bare `§4` names a section of nothing. A file of this tree is cited by
a heading, never by a number — none of its documents numbers its sections —
and only a file the tree holds counts: `source-architecture.md §7` (the plan
by its own name) and `CLAUDE.md §8` (a section CLAUDE.md never had) are
pointers into nothing too.

What breaks when this fails: a new comment or test cites such a label, points
into the planning directory, or names a section with no document. Write the
rule or the measurement the label stood for instead, or name the file whose
section it is; a reader of the tree has nothing else to go by.

What is not a label, and is left alone narrowly, each with its reason below:
hardware and hex addresses and UUID parts (told apart by the characters next to
the match), a quoted value (`name="A2"`), the codes of a `noqa` directive, the
ruff rule families the lint configuration names, the CIE illuminant D65, the
rows a checklist numbers and defines in place, and French prose in the locale
files (where "le fil" is an ordinary phrase). Vendored files and lockfiles are
not this tree's prose and are not read.
"""
import functools
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

# The planning document's name for its chapter on the wire ("Développeurs : le
# fil"), and the way its chapters were cited: `docs:` followed by a quoted
# chapter or by the document's own title ("docs: source architecture"), or
# "(the doc)" alone. A file of this tree is cited by its path, never after
# `docs:`.
_PLAN_CHAPTER = re.compile(
    r"\ble fil\b|Développeurs\s*:|\bdocs:\s+(?:[\"«]|source\b)|\(the doc\)"
)
# French prose, where "le fil" is a phrase and not a pointer.
FRENCH_PROSE_PREFIX = "frontend/src/locales/"

# A step of another plan: `P1-6`, `P2-8`, `(P1 fix)`, `(carried-over Phase 1
# fix)`, `the volume-ownership plan's phase 1`, `(plan phase 3)`. A range
# written with an en dash (`P1–P5`, a checklist's own rows) is not one, and
# neither is a bare `Phase 1:` numbering the steps of the function it sits in.
_PLAN_STEP = re.compile(
    r"\bP\d+-\d+\b|\b(?:P|[Pp]hase )\d+[a-z]? fix\b|\bplan(?:'s)?\s+phase\s+\d+"
)

# What may stand right before a section sign: a named document, optionally with
# sections of it already listed (`RFC 6762 §5.5, §6.7`). An RFC, a license, a
# file of this tree by its name (`CLAUDE.md`, `docs/architecture.md`,
# `design-system.css`) or by a Markdown link to it, each possibly closed by a
# backtick, a quote or an emphasis mark.
_NAMED_DOCUMENT = re.compile(
    r"(?:\bRFC\s?\d+|\b(?:L?GPL|AGPL)(?:v?\d(?:\.\d)?)?|(?P<file>\b[\w./-]*\w\.[a-z]{1,5})|\]\([^)\s]+\))"
    r"[`'\"*]?(?:\s*§\s*[\w.]+\s*,)*\s*$"
)
# What a file's section is named by: a heading, so a sign followed by a digit
# (`CLAUDE.md §8`, `spec.md §2`) is a number no file of this tree defines.
_NUMBERED_SECTION = re.compile(r"\s*\d")
# A section sign inside a Markdown link's label, the link naming the file:
# `[Architecture § App updates](architecture.md#app-updates)`.
_IN_LINK_LABEL = re.compile(r"^[^\[\]]*\]\(")
# The section a pointer names, for the report: `§7`, `§10.2.3`, `§ Security`.
_SECTION = re.compile(r"§\s*[\w.]*")
# Comment and string openers a wrapped line starts with before its text.
_LINE_OPENERS = " \t#/*-\"'`>"

# Upstream's Swift, edited only by a snapshot refresh, and generated lockfiles.
_NOT_READ_PREFIXES = ("backend/tests/contracts/vendor/",)
_NOT_READ_NAMES = {"package-lock.json"}
_NOT_READ_SUFFIXES = (".lock",)


@functools.lru_cache(maxsize=1)
def _tracked_basenames():
    """The file names the tree holds: the only files a section may belong to."""
    return frozenset(Path(name).name for name in _tracked_names())


def _section_has_a_document(line, at, previous):
    """Whether the section sign at `line[at]` follows a named document — on its
    own line, or at the end of the previous one when the sign opens a wrapped
    line ("see CLAUDE.md\\n§\"Persistence…\"", "(RFC 6762\\n§7.1)"). A file
    named before it must be one the tree holds, and its section a heading."""
    if _IN_LINK_LABEL.match(line[at + 1:]):
        return True
    before = line[:at]
    if not before.strip(_LINE_OPENERS):
        before = previous
    named = _NAMED_DOCUMENT.search(before.rstrip())
    if not named:
        return False
    if named.group("file") is None:
        return True
    return (Path(named.group("file")).name in _tracked_basenames()
            and not _NUMBERED_SECTION.match(line, at + 1))


def references_in(name, text):
    """Every opaque reference in one file's text, as `(path:line, label)`."""
    found = []
    letters_defined_here = SELF_NUMBERED.get(name, set())
    previous = ""
    for number, line in enumerate(text.splitlines(), start=1):
        where = f"{name}:{number}"
        if _PLANS_PATH.search(line) and name not in PLANS_PATH_ALLOWED:
            found.append((where, "docs/plans"))
        if not name.startswith(FRENCH_PROSE_PREFIX):
            found += [(where, m.group(0)) for m in _PLAN_CHAPTER.finditer(line)]
        found += [(where, m.group(0)) for m in _PLAN_STEP.finditer(line)]
        for at in (i for i, char in enumerate(line) if char == "§"):
            if not _section_has_a_document(line, at, previous):
                found.append((where, _SECTION.match(line, at).group(0).rstrip(".")))
        previous = line
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


def _tracked_names():
    names = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True,
    ).stdout.decode().split("\0")
    return [name for name in names if name]


def _tracked_text_files():
    for name in _tracked_names():
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


def test_a_simulated_section_or_step_reference_is_found():
    """The scanner finds a section pointer with no document before it, the
    planning document's chapter names, and another plan's step labels — and
    none of the section signs that name their document. Fails when a pattern
    stops matching a real citation, or starts refusing a legitimate one."""
    text = "\n".join([
        "# a discontinuity worth a `source/position` (docs: \"le fil\", §7)",
        '    """An empty string is never published: it is null (§2, §5)."""',
        "// the card rule (§9) decides",
        "are (docs: source architecture, \"Développeurs : le fil\"):",
        "# === Sessions (docs: source",
        "- Command params (P1-6): the typed contract",
        "# Network (P2-8): mount a CIFS or NFS share",
        "With set_online_after=True (P1 fix), the client stays offline",
        "the live DSP across the window (carried-over Phase 1 fix).",
        "the rule of source-architecture.md §7 and of `CLAUDE.md` §8",
        "see PLAN_MODAL_REDESIGN.md § Delta, since the volume-ownership plan's phase 1",
        "# A relative adjustment reaches a client (plan phase 3)",
        "# An episode that would not open stays to listen (the doc).",
        # Section signs that name their document:
        "statuses whose Retry-After means ask again (RFC 9110 §10.2.3,",
        "# RFC 6585 §4).",
        "a legacy unicast query (RFC 6762 §5.5, §6.7), the sender",
        "the source of the binary is offered under GPLv2 §3",
        "[docs/architecture.md § Security](docs/architecture.md#security)",
        "the mechanism is [docs/architecture.md](docs/architecture.md) § satellite-key",
        "pushes the satellites. [Architecture § App updates](architecture.md#app)",
        "dev server — see `CLAUDE.md` § *Dev-only vs production bugs*.",
        "Uses the schema_version protocol — see CLAUDE.md",
        '§"Persistence & the schema-version protocol".',
        "a step takes the secondary rung (design-system.css §",
        "rows P1–P5 are defined below; DSP1-2 is a part number",
    ])
    found = references_in("backend/example.py", text)
    assert found == [
        ("backend/example.py:1", 'docs: "'),
        ("backend/example.py:1", "le fil"),
        ("backend/example.py:1", "§7"),
        ("backend/example.py:2", "§2"),
        ("backend/example.py:2", "§5"),
        ("backend/example.py:3", "§9"),
        ("backend/example.py:4", "docs: source"),
        ("backend/example.py:4", "Développeurs :"),
        ("backend/example.py:4", "le fil"),
        ("backend/example.py:5", "docs: source"),
        ("backend/example.py:6", "P1-6"),
        ("backend/example.py:7", "P2-8"),
        ("backend/example.py:8", "P1 fix"),
        ("backend/example.py:9", "Phase 1 fix"),
        ("backend/example.py:10", "§7"),
        ("backend/example.py:10", "§8"),
        ("backend/example.py:11", "plan's phase 1"),
        ("backend/example.py:11", "§ Delta"),
        ("backend/example.py:12", "plan phase 3"),
        ("backend/example.py:13", "(the doc)"),
    ]
    # French prose names "le fil" as a phrase; a locale file keeps it, and
    # nothing else does.
    assert references_in("frontend/src/locales/french.json", '"x": "Reprendre le fil"') == []


def test_no_tracked_file_cites_an_undefined_label():
    """Every tracked text file is free of planning-document labels, of the
    planning directory's path, of sections with no document and of other
    plans' step labels. Fails with `path:line label` for each one."""
    files = dict(_tracked_text_files())
    # A broken listing or reader must fail here, not pass on an empty tree.
    assert len(files) > 1000, f"only {len(files)} tracked text files read"
    for must_be_read in ("CLAUDE.md", "docs/architecture.md"):
        assert must_be_read in files, f"{must_be_read} was not read"
    for tree in ("backend/", "frontend/src/", "frontend/tests/", "milo-client/"):
        assert any(name.startswith(tree) for name in files), f"nothing under {tree} was read"
    # The section rule must meet legitimate section signs (RFCs, repo docs) to
    # mean anything: a reader that lost them would pass it on an empty set.
    assert sum(text.count("§") for text in files.values()) > 50, "too few section signs read"

    found = [hit for name, text in files.items() for hit in references_in(name, text)]
    assert not found, "opaque references to plans the tree does not hold:\n" + "\n".join(
        f"  {where}  {label}" for where, label in found
    )
