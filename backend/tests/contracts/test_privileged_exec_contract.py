"""Offline contract test: the `sudo` calls the code issues ⇄ the sudoers policy.

Invariant #1 says privileged exec is centralized: the two FastAPI apps in this
checkout shell a handful of pinned `/usr/local/bin/milo-*` helpers plus
`systemctl`, and the permission to do so comes from three NOPASSWD policy files
deployed from `rootfs/`. **Nothing compared the two.**

A divergence there is invisible everywhere CI can look. There is no import to
fail and no route to 404 — sudo simply refuses, and the symptom is a reboot that
does not happen, an update that does not deploy, a share that does not mount.
It appears on a real unit, at the moment someone reboots or applies an update,
which is why it belongs in a test rather than in the manual checklist.

The two sides are asymmetric and that is the point:

  * the **server** policy grants bare command paths, so any argv is permitted
    once the binary matches — the contract there is which binaries. The
    backend is therefore root by construction (a bare `systemctl`,
    `milo-deploy-update`, `milo-set-password`), and what keeps the other
    `milo` processes out of it is `NoNewPrivileges=true` on their units,
    pinned by `tests/architecture/test_unit_hardening.py`;
  * the **satellite** policy grants argument-scoped commands
    (`systemctl stop milo-client-snapclient.service`), so a verb or a unit name
    that moves on one side alone *is* a denial. `routes/snapclient.py` already
    carries a comment saying "sudoers allows stop/start, not restart"; this test
    is that comment made mechanical.

Both directions are asserted: every command the code issues must be permitted,
and every granted command must still have a caller — a stale NOPASSWD line is a
privilege nobody needs. Every extractor asserts its own output is non-trivial
first, so a broken parse fails loudly instead of passing on an empty surface
(same doctrine as the Milo-Mac and milo-client contract tests).

When this fails: fix the side that moved. Do not add a grant to silence it
without checking the caller is one the appliance should have.
"""
import ast
import functools
import itertools
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SATELLITE_APP = REPO_ROOT / "milo-client" / "app"

# `sudo` resolves a bare command name through PATH; the policy names the
# absolute path. This is the translation between the two sides, so a new bare
# command must be declared here rather than silently comparing unequal strings.
BARE_COMMAND_PATHS = {
    "systemctl": "/usr/bin/systemctl",
}

# Each code tree runs as one user, and only that user's policies apply to it.
# `milo-ir-remote` is a second file for the same user, deployed by
# provisioning/ir-remote.sh::install_ir_helpers where pi-gen copies `milo-backend`
# itself.
TREES = {
    "backend": {
        "sources": [REPO_ROOT / "backend"],
        "user": "milo",
        "policies": [
            REPO_ROOT / "rootfs" / "etc" / "sudoers.d" / "milo-backend",
            REPO_ROOT / "rootfs" / "etc" / "sudoers.d" / "milo-ir-remote",
        ],
        "rootfs": REPO_ROOT / "rootfs",
    },
    "milo-client": {
        "sources": [REPO_ROOT / "milo-client" / "app"],
        "user": "milo-client",
        "policies": [
            REPO_ROOT / "milo-client" / "rootfs" / "etc" / "sudoers.d" / "milo-client",
        ],
        "rootfs": REPO_ROOT / "milo-client" / "rootfs",
    },
}

# An argument the extractor could not resolve to a literal.
UNKNOWN = None

# The heads an argv is recognized by. A call that reaches sudo any other way
# (a shell string, a path held in a variable) is not seen at all.
SUDO_SPELLINGS = ("sudo", "/usr/bin/sudo")


# --------------------------------------------------------------------------- #
# Side A: the argv the code builds.
# --------------------------------------------------------------------------- #

def _modules(tree_name: str) -> list[Path]:
    return sorted(
        p
        for root in TREES[tree_name]["sources"]
        for p in root.rglob("*.py")
        if "__pycache__" not in p.parts and "tests" not in p.parts
    )


def _string_of(node) -> str | None:
    """The string a node denotes, for the few literal spellings that appear.

    `Path("/usr/local/bin/x")` and `str(APPLY_HELPER)` are how two call sites
    write a helper path; unwrapping them here keeps the call sites free to say
    what they mean instead of matching what a parser can read.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call):
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
        if name in ("Path", "str") and node.args:
            return _string_of(node.args[0])
    return None


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level `NAME = "literal"` (or `= Path("literal")`)."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            value = _string_of(node.value)
            if value is None:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = value
    return out


@functools.cache
def _constant_index() -> dict[str, dict[str, str]]:
    """Dotted module name → its module-level string constants, repo-wide.

    Needed because the two helper paths that matter most are declared once and
    imported: `DEPLOY_UPDATE_CMD` and `MILO_{,U}MOUNT_CMD` live in
    `backend/config/constants.py`, which is exactly the right place for them.
    """
    index = {}
    for root in (REPO_ROOT / "backend", REPO_ROOT / "milo-client"):
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            consts = _module_constants(ast.parse(path.read_text(encoding="utf-8")))
            index[".".join(path.relative_to(REPO_ROOT).with_suffix("").parts)] = consts
            # The satellite app runs with `milo-client/app` on sys.path, so it
            # spells its own imports `services.snapclient`, never the path from
            # the repo root. Keyed only the latter way, the index resolves no
            # satellite constant at all and every call carrying one reads as an
            # unresolved argument — which fails loudly, but pushes the next
            # author to restate a unit name rather than import it.
            if path.is_relative_to(SATELLITE_APP):
                index[".".join(path.relative_to(SATELLITE_APP).with_suffix("").parts)] = consts
    return index


def _imported_constants(tree: ast.Module, path: Path, index) -> dict[str, str]:
    """The string constants this module pulls in with `from … import NAME`."""
    package = list(path.relative_to(REPO_ROOT).parent.parts)
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            base = package[: len(package) - node.level + 1]
            dotted = ".".join(base + ([node.module] if node.module else []))
        else:
            dotted = node.module or ""
        source = index.get(dotted)
        if not source:
            continue
        for alias in node.names:
            if alias.name in source:
                out[alias.asname or alias.name] = source[alias.name]
    return out


def _param_values(tree: ast.Module, consts: dict[str, str]) -> dict[str, set[str]]:
    """Parameter name → the strings THIS module passes to it.

    `storage.py` reaches both helpers through one `_run_helper(helper, ...)`,
    with `MILO_MOUNT_CMD` / `MILO_UMOUNT_CMD` supplied by callers a frame up in
    the same module. Deliberately per-module, as in the milo-client contract
    test: pooling parameter names repo-wide invents calls no code makes.
    """
    def value_of(node):
        literal = _string_of(node)
        if literal is not None:
            return literal
        if isinstance(node, ast.Name):
            return consts.get(node.id)
        return None

    params_of = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            params_of[node.name] = [p.arg for p in a.posonlyargs + a.args + a.kwonlyargs]

    values: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        declared = params_of.get(fname)
        if not declared:
            continue
        # `self` occupies slot 0 of a method's parameter list but is never
        # passed at a call site, so positional args start one slot later.
        offset = 1 if declared and declared[0] in ("self", "cls") else 0
        for i, arg in enumerate(node.args):
            slot = i + offset
            if slot < len(declared):
                literal = value_of(arg)
                if literal is not None:
                    values.setdefault(declared[slot], set()).add(literal)
        for kw in node.keywords:
            if kw.arg in declared:
                literal = value_of(kw.value)
                if literal is not None:
                    values.setdefault(kw.arg, set()).add(literal)
    return values


def _loop_values(tree: ast.Module) -> dict[str, set[str]]:
    """Loop target → the literals it iterates over.

    `for action in ["stop", "start"]` in routes/snapclient.py is the one place
    a satellite argv is written as a set rather than a constant — and it is the
    argument the policy scopes on, so leaving it unresolved would leave the
    sharpest case unverified.
    """
    values: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.AsyncFor)):
            continue
        if not isinstance(node.target, ast.Name):
            continue
        if not isinstance(node.iter, (ast.List, ast.Tuple, ast.Set)):
            continue
        literals = {_string_of(e) for e in node.iter.elts}
        if None not in literals:
            values.setdefault(node.target.id, set()).update(literals)
    return values


def _resolve(node, consts, params, loops) -> set:
    """Every value an argv element can take, or {UNKNOWN} if unresolvable."""
    literal = _string_of(node)
    if literal is not None:
        return {literal}
    if isinstance(node, ast.Name):
        if node.id in consts:
            return {consts[node.id]}
        for table in (loops, params):
            if node.id in table:
                return set(table[node.id])
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str":
        if node.args:
            return _resolve(node.args[0], consts, params, loops)
    return {UNKNOWN}


def _argv_sources(tree: ast.Module):
    """Every node sequence that looks like an argv starting with `sudo`.

    Both spellings are in use: the args of a `create_subprocess_exec(...)` call,
    and a `cmd = ["sudo", ...]` list splatted into one (keymap_writer.py). Match
    on the shape rather than on the callee so a new spawn helper is covered too.
    """
    for node in ast.walk(tree):
        elements = None
        if isinstance(node, ast.Call) and node.args:
            elements = node.args
        elif isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            elements = node.elts
        if elements and _string_of(elements[0]) in SUDO_SPELLINGS:
            yield elements[1:]


def _argvs_of(tree: ast.Module, consts, params, loops) -> list[tuple]:
    """Every argv one module hands to sudo, sudo's own options dropped.

    An argv whose command hides behind a starred tail — `("sudo", *argv)` in a
    `_sudo(*argv)` helper — comes out as `(UNKNOWN,)` rather than not at all:
    the commands that helper runs are spelled at its call sites, which this
    reads nowhere, so dropping it would let any of them pass unchecked.
    """
    found = []
    for elements in _argv_sources(tree):
        words = []
        for element in elements:
            if isinstance(element, ast.Starred):
                break  # unknown tail — the args below are all we can see
            words.append(_resolve(element, consts, params, loops))
        # Drop sudo's own options (`-n`); the command starts after them.
        while words and words[0] and all(
            w is not None and w.startswith("-") for w in words[0]
        ):
            words.pop(0)
        if not words:
            words = [{UNKNOWN}]
        found.extend(itertools.product(*words))
    return found


@functools.cache
def _issued_argvs(tree_name: str) -> list[tuple[str, tuple]]:
    """(location, argv) for every privileged call the tree makes."""
    found = []
    index = _constant_index()
    for path in _modules(tree_name):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        consts = {**_imported_constants(tree, path, index), **_module_constants(tree)}
        rel = str(path.relative_to(REPO_ROOT))
        for argv in _argvs_of(tree, consts, _param_values(tree, consts), _loop_values(tree)):
            found.append((rel, argv))
    return found


# --------------------------------------------------------------------------- #
# Side B: what the policy grants.
# --------------------------------------------------------------------------- #

# sudoers' spelling of "this command with no argument at all" — the grant of a
# satellite helper that reads its input on stdin (milo-client-set-timezone,
# milo-client-join-wifi, milo-set-password).
NO_ARGUMENTS = ('""',)


def _grants(policy: Path, user: str) -> list[tuple[str, tuple]]:
    """(command, args) for each NOPASSWD line, args empty meaning "any", NO_ARGUMENTS "none"."""
    out = []
    for line in policy.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith(f"{user} ") or "NOPASSWD:" not in line:
            continue
        words = line.split("NOPASSWD:", 1)[1].split()
        if words:
            out.append((words[0], tuple(words[1:])))
    return out


def _all_grants(tree_name: str) -> list[tuple[str, str, tuple]]:
    user = TREES[tree_name]["user"]
    return [
        (str(policy.relative_to(REPO_ROOT)), command, args)
        for policy in TREES[tree_name]["policies"]
        for command, args in _grants(policy, user)
    ]


def _matches(argv: tuple, command: str, args: tuple) -> bool:
    """Does this grant permit this argv, under sudoers' matching rules?

    A grant with no arguments permits any; a grant of `""` permits the bare
    command and nothing else; a grant with arguments permits that exact
    argument vector and nothing else. An argv the extractor could not
    fully resolve therefore cannot satisfy an argument-scoped grant — better an
    honest failure than a match that assumed the unknown was right.
    """
    issued_command = BARE_COMMAND_PATHS.get(argv[0], argv[0])
    if issued_command != command:
        return False
    if not args:
        return True
    if args == NO_ARGUMENTS:
        return len(argv) == 1
    return tuple(argv[1:]) == args


# --------------------------------------------------------------------------- #
# Non-triviality: a broken extractor must fail here, not pass everything below.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("tree_name", sorted(TREES))
def test_extractors_see_a_real_surface(tree_name):
    """An empty read of either side would make both rules below vacuous."""
    issued = _issued_argvs(tree_name)
    granted = _all_grants(tree_name)
    assert len(issued) >= 5, f"{tree_name}: only {len(issued)} sudo call(s) extracted"
    assert len(granted) >= 5, f"{tree_name}: only {len(granted)} NOPASSWD grant(s) parsed"
    assert all(argv for _, argv in issued), f"{tree_name}: an empty argv was extracted"


def test_argument_scoped_grants_stay_argument_scoped():
    """The satellite half is the one that can actually deny — prove it is read.

    This is the one degradation both rules below would survive in silence: if
    the parser dropped a grant's arguments, every grant would read as "any
    args", both directions would still pass, and `systemctl restart` on a unit
    the policy never named would look permitted. Everything else — an argv left
    UNKNOWN, a grant missed — makes one of the two rules fail loudly, which is
    the safe direction. What no rule here can see is a call that does not
    start with a `SUDO_SPELLINGS` literal: a shell string, or a sudo path held
    in a variable, is not extracted at all.
    """
    scoped = [g for g in _all_grants("milo-client") if g[2]]
    assert len(scoped) >= 3, f"only {len(scoped)} argument-scoped grants parsed"

    # ...and the matcher must be discriminating on them, not a rubber stamp:
    # the same command with a verb the policy did not grant stays refused.
    _, command, args = scoped[0]
    assert _matches((command, *args), command, args)
    assert not _matches((command, "restart", *args[1:]), command, args)


def test_a_no_argument_grant_permits_the_bare_command_only():
    """`""` is the narrowest grant sudoers has, and it must be read as one.

    Read as the literal argument `""`, it matched no call and the grant
    reported itself unused; read as "any", an argv carrying the zone would pass
    here and be refused by sudo on the satellite.
    """
    no_argument = [g for g in _all_grants("milo-client") if g[2] == NO_ARGUMENTS]
    assert no_argument, "no `\"\"` grant parsed in the satellite policy"

    _, command, args = no_argument[0]
    assert _matches((command,), command, args)
    assert not _matches((command, "Europe/Paris"), command, args)
    assert not _matches((command, '""'), command, args)


# How a helper refuses any argument: its input comes on stdin, so an argv is a
# caller that bypassed the route.
ARGV_REFUSAL = re.compile(r"^\s*if \(\(\s*\$#\s*!=\s*0\s*\)\)", re.MULTILINE)


def test_a_helper_that_refuses_every_argument_is_granted_none():
    """The grant and the helper must agree that no argv belongs on this command line.

    A bare grant on a helper that takes none lets sudo run it as root with an
    argv nobody vetted — the helper's own check is then the only one, and a
    future edit of the helper that starts reading `$1` reads it unvetted. A `""`
    grant on a helper that does not refuse one leaves the helper wide open
    wherever it is reached without that policy (by hand, as root, or through the
    server's bare grant of the twin `milo-set-password`). Satellite only: the
    server's grants are scoped by binary alone, and narrowing one closes nothing
    while a bare `systemctl` stands (invariant 5).
    """
    rootfs = TREES["milo-client"]["rootfs"]
    grants = [(c, a) for _, c, a in _all_grants("milo-client") if c.startswith("/usr/local/bin/")]
    refusing = {
        command for command, _ in grants
        if ARGV_REFUSAL.search((rootfs / command.lstrip("/")).read_text(encoding="utf-8"))
    }
    granted_none = {command for command, args in grants if args == NO_ARGUMENTS}
    # Either side read empty would let the equality below pass on nothing.
    assert refusing and granted_none, (
        f"refusing {sorted(refusing)}, granted none {sorted(granted_none)}: an extractor is broken"
    )

    assert refusing == granted_none, (
        f"refuse every argument but are not granted with none: {sorted(refusing - granted_none)}; "
        f"granted none but accept an argument: {sorted(granted_none - refusing)}"
    )
    # sudo matches each line on its own, so a second, bare line for the same
    # helper is the one that matches every argv the `""` line refuses.
    granted_more = {command for command, args in grants if args != NO_ARGUMENTS}
    assert not refusing & granted_more, (
        f"refuse every argument but hold a grant besides `\"\"`: {sorted(refusing & granted_more)}"
    )


@pytest.mark.parametrize(
    "source",
    [
        'async def _sudo(*argv):\n'
        '    await asyncio.create_subprocess_exec("sudo", *argv)\n',
        'async def _sudo(*argv):\n'
        '    await asyncio.create_subprocess_exec("sudo", "-n", *argv)\n',
        'def _sudo(*argv):\n'
        '    cmd = ["sudo"] + list(argv)\n',
    ],
    ids=["starred", "option-then-starred", "list-concatenation"],
)
def test_a_sudo_helper_with_a_starred_tail_is_not_dropped(source):
    """A `_sudo(*argv)` helper must reach the contract, not vanish from it.

    Its command is spelled at the helper's call sites, which the extractor
    does not follow; dropped, `_sudo("systemctl", "restart", unit)` on the
    satellite — where only stop and start are granted — would pass CI and be
    refused by sudo on the unit.
    """
    argvs = _argvs_of(ast.parse(source), {}, {}, {})
    assert argvs, "the helper's sudo call was not extracted at all"
    assert all(argv[0] is UNKNOWN for argv in argvs), argvs


@pytest.mark.parametrize("tree_name", sorted(TREES))
def test_every_sudo_argv_has_a_resolvable_command(tree_name):
    """A command the extractor cannot name is one no grant can be checked against."""
    unnamed = [where for where, argv in _issued_argvs(tree_name) if argv[0] is UNKNOWN]
    assert not unnamed, (
        "these sudo calls hide their command (a tail the extractor cannot read, "
        "or an unresolved name after sudo's options); write the argv literally at the call "
        "site, or resolve the helper's call sites in this extractor:\n"
        + "\n".join(unnamed)
    )


# --------------------------------------------------------------------------- #
# The contract.
# --------------------------------------------------------------------------- #

# Matched on the rule text rather than parsed: what the test below is about is
# the *order* of these two kinds of rule, not their contents.
WITHDRAWAL_TAG = "PASSWD: ALL"
GRANT_TAG = "NOPASSWD:"


def _rules_in_read_order(tree_name: str) -> list[tuple[str, str]]:
    """(policy name, rule line) for one user, in the order sudo reads them.

    sudo reads /etc/sudoers.d in lexical filename order, so sorting a tree's
    policies by name reproduces it — `milo-backend` before `milo-ir-remote`.
    """
    user = TREES[tree_name]["user"]
    return [
        (policy.name, line.strip())
        for policy in sorted(TREES[tree_name]["policies"], key=lambda p: p.name)
        for line in policy.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith(f"{user} ")
    ]


@pytest.mark.parametrize("tree_name", sorted(TREES))
def test_the_image_blanket_grant_is_withdrawn_before_any_grant(tree_name):
    """Without this rule the policy is decorative; below the grants it is fatal.

    Raspberry Pi OS ships /etc/sudoers.d/010_pi-nopasswd — a conffile of
    raspberrypi-sys-mods, whose username userconf-pi rewrites on first boot — so
    on a unit whose appliance account is also the image's first user it reads
    `<user> ALL=(ALL) NOPASSWD: ALL`. Measured on a satellite: `sudo -n true`
    succeeded for `milo-client`, which makes every argument-scoped grant the
    tests above check decorative, and leaves invariant #1 true in the tree and
    false on the appliance. Each policy therefore re-tags the same commands
    PASSWD; it is read after 010_… and sudo applies the *last* matching rule.

    The order is the whole fix. Below the grants that rule becomes the last
    match for those too, and a satellite is then asked for a password no service
    can type — while the repair path, `milo-client-deploy-update`, needs the
    very sudo it just lost.
    """
    rules = _rules_in_read_order(tree_name)
    withdrawals = [
        i for i, (_, rule) in enumerate(rules)
        if GRANT_TAG not in rule and rule.endswith(WITHDRAWAL_TAG)
    ]
    grants = [i for i, (_, rule) in enumerate(rules) if GRANT_TAG in rule]

    assert grants, f"{tree_name}: no grant parsed — the extractor is broken"
    assert withdrawals, (
        f"{tree_name}: no `{TREES[tree_name]['user']} … {WITHDRAWAL_TAG}` rule — "
        "the image's blanket NOPASSWD makes every grant here decorative"
    )
    assert max(withdrawals) < min(grants), (
        f"{tree_name}: the `{WITHDRAWAL_TAG}` rule must precede every grant; "
        f"{rules[max(withdrawals)]} comes after {rules[min(grants)]}"
    )


@pytest.mark.parametrize("tree_name", sorted(TREES))
def test_every_privileged_call_is_permitted(tree_name):
    """A call the policy does not grant is a silent denial on a real unit."""
    granted = _all_grants(tree_name)
    denied = [
        f"{where}: sudo {' '.join('?' if w is UNKNOWN else w for w in argv)}"
        for where, argv in _issued_argvs(tree_name)
        if not any(_matches(argv, command, args) for _, command, args in granted)
    ]
    assert not denied, (
        "these privileged calls are not permitted by "
        f"{', '.join(str(p.relative_to(REPO_ROOT)) for p in TREES[tree_name]['policies'])}:\n"
        + "\n".join(denied)
    )


@pytest.mark.parametrize("tree_name", sorted(TREES))
def test_every_grant_still_has_a_caller(tree_name):
    """A NOPASSWD line no code uses is a privilege granted for nothing."""
    issued = [argv for _, argv in _issued_argvs(tree_name)]
    unused = [
        f"{policy}: {command} {' '.join(args)}".strip()
        for policy, command, args in _all_grants(tree_name)
        if not any(_matches(argv, command, args) for argv in issued)
    ]
    assert not unused, "these grants have no caller left; drop them:\n" + "\n".join(unused)


@pytest.mark.parametrize("tree_name", sorted(TREES))
def test_granted_helpers_are_shipped_by_the_same_tree(tree_name):
    """A grant naming a helper the tree does not ship is a 203/EXEC on hardware.

    Only the `milo-*` helpers are checked; `systemctl` and friends come from the
    OS. This is the deployment half of the same failure: the policy can be
    perfect and the command still absent.
    """
    rootfs = TREES[tree_name]["rootfs"]
    helpers = [
        command for _, command, _ in _all_grants(tree_name)
        if command.startswith("/usr/local/bin/")
    ]
    assert helpers, f"{tree_name}: no milo-* helper grant parsed"
    missing = [c for c in helpers if not (rootfs / c.lstrip("/")).is_file()]
    assert not missing, (
        f"granted but not shipped by {rootfs.relative_to(REPO_ROOT)}: " + ", ".join(missing)
    )


# --------------------------------------------------------------------------- #
# Syntax: every policy must be one sudo will read at all.
# --------------------------------------------------------------------------- #

ALL_POLICIES = sorted({p for tree in TREES.values() for p in tree["policies"]})
ALL_POLICIES_BY_NAME = {p.name: p for p in ALL_POLICIES}


@functools.cache
def _visudo() -> str:
    """visudo is the parser sudo itself uses; nothing else can stand in for it.

    Absent, the check below fails rather than skips: a syntax check that
    quietly stops running is the gap it exists to close. It is present on the
    CI runner and on the appliance.
    """
    found = shutil.which("visudo") or (
        "/usr/sbin/visudo" if Path("/usr/sbin/visudo").is_file() else None
    )
    if found is None:
        pytest.fail("visudo not found on PATH or at /usr/sbin/visudo (install sudo)")
    return found


def _visudo_check(policy: Path) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            [_visudo(), "-c", "-f", str(policy)],
            capture_output=True, text=True, check=False, timeout=30,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"visudo did not answer within 30 s on {policy}")


def test_every_shipped_policy_is_checked():
    """A policy file TREES does not list would reach a unit unparsed."""
    shipped = sorted(
        policy
        for tree in TREES.values()
        for policy in (tree["rootfs"] / "etc" / "sudoers.d").iterdir()
    )
    assert shipped, "no sudoers policy found under either rootfs/etc/sudoers.d"
    assert ALL_POLICIES == shipped, (
        f"TREES lists {[str(p.relative_to(REPO_ROOT)) for p in ALL_POLICIES]}, "
        f"the trees ship {[str(p.relative_to(REPO_ROOT)) for p in shipped]}"
    )


@pytest.mark.parametrize(
    "policy", ALL_POLICIES, ids=[str(p.relative_to(REPO_ROOT)) for p in ALL_POLICIES]
)
def test_every_policy_parses_under_visudo(policy):
    """A policy visudo rejects is one sudo skips whole, every grant in it lost.

    `_grants` reads the files by splitting lines, which a dropped parenthesis
    does not disturb, so every other test here stays green on a policy sudo
    cannot parse. The only parsers run after the merge: the pi-gen stage
    `03-configure` and `provisioning/ir-remote.sh` fail the tag build,
    `milo-first-boot` leaves the policy out with a warning, `milo-deploy-update`
    refuses the server copy, and `milo-client-deploy-update::install_sudoers`
    refuses the satellite copy after the units are already installed — a
    partial deploy that keeps the old policy, where a moved grant then asks
    `sat restart` for a password.
    """
    result = _visudo_check(policy)
    assert result.returncode == 0, (
        f"visudo rejects {policy.relative_to(REPO_ROOT)}:\n{result.stdout}{result.stderr}"
    )


def test_the_visudo_check_rejects_a_malformed_policy(tmp_path):
    """The syntax check must be able to go red, on exactly what `_grants` misses."""
    good = ALL_POLICIES_BY_NAME["milo-client"]
    text = good.read_text(encoding="utf-8")
    broken_text = text.replace("(root) NOPASSWD:", "(root NOPASSWD:", 1)
    assert broken_text != text, "no `(root) NOPASSWD:` rule left to break in milo-client"

    broken = tmp_path / "milo-client"
    broken.write_text(broken_text, encoding="utf-8")
    assert _grants(broken, "milo-client") == _grants(good, "milo-client")
    assert _visudo_check(broken).returncode != 0
