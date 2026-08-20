# backend/tests/architecture/test_silent_failure.py
"""Structural guardrail against the class "a failure that presents as success".

The 2026-08-14 backend audit found eleven of these and phases 5 and 6 fixed
them (`ccc1cfe7`, `21d218a0`). Nothing prevented a twelfth: a fan-out reached
three speakers out of four, or a subprocess exited non-zero, and the route
answered 200 `"status": "success"` with an empty journal behind it. The owner's
only signal was that one speaker kept the old curve.

What makes the class survive every existing check is *which channel the failure
travels on*. The error-handling doctrine's three markers —
`@handle_errors(default=…)`, `contextlib.suppress(Type)` and a background-loop
body's `except Exception` — all govern the **exception** channel, and so does
every guardrail built on them. The two constructs below are the ones that take the
failure *off* that channel:

  * ``asyncio.gather(return_exceptions=True)`` promises no exception will be
    raised; it hands them back as ordinary return values instead.
  * a subprocess reports by exit status, which is not an exception at all.
  * ``@handle_errors(default=False)`` catches every Exception, so a function
    wearing it *cannot* raise: its bool is the only failure channel it has.

So the documented best-effort markers are exempt here *by construction, and
by construction they exempt nothing*: they cannot see either outcome. That is
not a loophole to close, it is the reason this file exists — and it is measured,
not asserted: `ScreenController._screen_cmd`, occurrence 6.7, carried
`@handle_errors(default=None)` while ignoring the exit code of the very command
whose success it reported.

Three rules, each decidable without a call graph — the first two from one
function's AST, the third from one class's:

  1. A parallel fan-out must not discard what it collected (occurrences 5.1,
     5.2 — `set_zone_eq` returned a hardcoded True over a discarded gather).
  2. A spawned process must have either its exit status or its output read
     (occurrences 6.2 and 6.7 — the rollback's two npm steps, and the backlight
     write whose exit code decided nothing).
  3. A sealed function must not answer with a literal over a *sibling's*
     discarded answer (the 2026-08-20 sweep's S1/S2 — `apply_zone_crossover`
     dropped six `_set_client_filter` verdicts and returned True, and
     `set_zone_crossover_frequency` dropped that one and returned True in turn,
     so the crossover route answered 200 with a frequency the subwoofer never
     took). Added by that sweep; verified red against the real tree with its
     exemptions removed, and both ways on hand-written source below.

Four of the eleven, all four re-checked red by restoring the pre-fix code on
2026-08-20. The fifth subprocess occurrence, 6.8, is deliberately outside rule 2
and stays a review concern: the BlueALSA monitor *did* consume its stdout — the
read loop is the whole source — and its exit code *was* read in `stop()`. What
it lacked was a report on the way out of that loop, which is behaviour, not
shape. A rule flagging it would have to flag every `readline()` loop in the tree.

Both trees are covered: `milo-client/app/` spawns and fans out too, and the
satellite is where a silent failure is least visible — it has no log surface in
the UI at all.

**The consumer half of variant (a) — "a bool nobody tested" — is deliberately
not here, because it has no sound static form in this codebase.** Measured on
2026-08-20 rather than assumed, at four successive narrowings:

  * every discarded call to a name declared `-> bool`: 237 sites across 180
    modules — no signal, and the name matching alone is wrong (`applied.remove`
    resolves to `MusicLibraryShares.remove`).
  * restricted to route handlers, where the false success actually reaches a
    consumer: 19, and 12 after dropping names that shadow a builtin method.
  * every filter that reduces those 12 to a defensible residue also blinds the
    check to one of the eleven: exempting a call inside `try/except` + log loses
    6.5 (the snapclient restart, which logged and answered "services restarted"
    anyway); exempting `api_error_handler`/`_catalog_errors` bodies loses 6.6.
  * inverting it — flagging a discarded call to a function *sealed* by
    `@handle_errors(default=False)`, whose return value is provably the only
    failure channel it has — gives 38 sites, because that decorator is this
    repo's marker for legitimate fail-open, not for a verdict worth reading.

Rule 3 is the fifth narrowing, and the one that holds: it keeps the sealed-callee
half (which is sound) and drops the resolution problem entirely by looking only
at `self.<m>()`, which binds inside its own class with nothing to guess. It then
asks about the *caller* rather than the call — does this function claim success
with a literal, having dropped a verdict its sibling computed? — which is what
turns 38 judgement calls into three sites and no judgement. What it deliberately
does not reach is a dropped verdict from another service, which stays a review
concern for the same reason as the rest of this paragraph.

The blocker is structural, not effort: services are reached through instance
attributes under dict-based DI (`source.station_data.add_custom_station`), so
no offline resolver can bind a call to a class — the same limit
`test_wire_conventions.py` records for its `SUCCESS_PRODUCERS` scan. There,
over-approximating by name is harmless because the producer set is small and
specific; here it is 289 names including `remove`, `start`, `stop` and `check`.
A rule with a twelve-entry exemption list, six of whose entries are judgement
calls, would launder those judgements as verified. Whether a discarded bool is
best-effort stays a review concern — as does whether a fan-out that *does* read
its results reads them correctly.

Doctrine note (same as `test_service_wiring.py` and the contract tests): every
extractor asserts its own output is non-trivial first, so a broken parse fails
loudly instead of passing on an empty surface.
"""
import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
SATELLITE_ROOT = REPO_ROOT / "milo-client" / "app"

SPAWN_FACTORIES = {"create_subprocess_exec", "create_subprocess_shell"}
# The three ways a process hands its output to the caller. Reading any of them
# is reading the outcome: `systemctl is-active` reports "inactive" on stdout
# with a non-zero exit, so its parse — not its exit code — is the check.
OUTPUT_CHANNELS = {"communicate", "stdout", "stderr"}


def _production_modules():
    """Every production module of both applications."""
    out = []
    for root in (BACKEND_ROOT, SATELLITE_ROOT):
        for path in sorted(root.rglob("*.py")):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            out.append(path)
    return out


_TREES = {}
for _path in _production_modules():
    try:
        _TREES[_path] = ast.parse(_path.read_text(encoding="utf-8"))
    except SyntaxError as exc:  # pragma: no cover - a broken tree is a real failure
        raise AssertionError(f"cannot parse {_path}: {exc}") from exc


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def test_extractor_sees_both_applications():
    """A collection bug must fail here, not silently pass every rule below."""
    assert len(_TREES) >= 150, f"only {len(_TREES)} production modules parsed"
    assert any("milo-client/" in _rel(p) for p in _TREES), (
        "no satellite module parsed — the second tree is not being walked"
    )


# --------------------------------------------------------------------------- #
# Shared AST plumbing.
# --------------------------------------------------------------------------- #

def _parents(tree):
    """child node -> its direct parent, for walking back up."""
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _enclosing(node, parents, types):
    """Innermost ancestor of `node` of one of `types`, or None."""
    cursor = parents.get(node)
    while cursor is not None:
        if isinstance(cursor, types):
            return cursor
        cursor = parents.get(cursor)
    return None


_FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)


def _site(path, node, parents):
    """`file::function` — the stable identity of a call site.

    Keyed on the enclosing function rather than the line so an exemption
    survives edits above it; the line is added to the failure message instead.
    """
    fn = _enclosing(node, parents, _FUNC)
    return f"{_rel(path)}::{fn.name if fn else '<module>'}"


def _in_window(node, window) -> bool:
    """True if `node` falls inside a (first line, last line) window.

    A local name is only itself between its binding and the next one: the
    rollback assigned `proc` three times in one function, and the third — the
    pip install — checked its exit code, which made the two npm spawns above it
    read as covered by a name that was no longer theirs.
    """
    if window is None:
        return True
    line = getattr(node, "lineno", None)
    return line is not None and window[0] <= line <= window[1]


def _reads_expression(scope, expression: str, attribute: str, window=None) -> bool:
    """True if `scope` reads `<expression>.<attribute>` inside `window`.

    Unparse-compared rather than pattern-matched on the node shape, so
    `proc.returncode` and `self._process.returncode` are one check.
    """
    return any(
        isinstance(node, ast.Attribute)
        and node.attr == attribute
        and ast.unparse(node.value) == expression
        and _in_window(node, window)
        for node in ast.walk(scope)
    )


# --------------------------------------------------------------------------- #
# Rule 1 — a parallel fan-out must not discard what it collected.
# --------------------------------------------------------------------------- #

# One line per entry, and each must still match something (asserted below).
EXEMPT_FANOUTS = {
    "backend/api/routing.py::_push_snapclient_config_to_remotes":
        "each arm is a local closure that catches and logs its own satellite by name",
}


def _fanouts():
    """Every `asyncio.gather(..., return_exceptions=True)` in both trees.

    The flag is what makes this decidable without a call graph: it is a promise
    that nothing will raise, so the returned list is the *only* place a failure
    can still be. A site that asks for that list and drops it has asked for the
    failures in order to throw them away.
    """
    found = []
    for path, tree in _TREES.items():
        parents = _parents(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "gather":
                continue
            if not any(
                kw.arg == "return_exceptions"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
                for kw in node.keywords
            ):
                continue
            found.append((path, node, parents))
    return found


FANOUTS = _fanouts()
# A floor, not a count (13 as of 2026-08-20): a parser that matched nothing must
# fail here rather than pass over an empty surface.
assert len(FANOUTS) >= 8, f"only {len(FANOUTS)} gather fan-outs found — extractor broken?"
assert any("multiroom" in _rel(p) for p, _, _ in FANOUTS), (
    "the EQ/multiroom fan-outs — the ones this rule exists for — were not seen"
)


def _is_cancellation_drain(fn) -> bool:
    """True if `fn` cancels tasks before gathering them.

    `BackgroundTaskSet.cancel_all` and `ir_remote`'s learn-mode teardown gather
    tasks they have just cancelled: the exceptions the flag holds back are the
    CancelledErrors they caused on purpose, and there is no outcome to read.
    """
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "cancel"
        for node in ast.walk(fn)
    )


def _fanout_outcome(node, parents, fn):
    """Why this fan-out's results are unreadable, or None if they are read.

    Three shapes count as read: bound to a name something else in the function
    loads, returned to a caller, or consumed inline (`zip(...)`, a subscript, a
    comprehension). Only a bare expression statement, or a binding nothing ever
    loads again, is a collected outcome nobody can see.
    """
    parent = parents.get(node)
    if isinstance(parent, ast.Await):
        parent = parents.get(parent)

    if isinstance(parent, ast.Expr):
        if fn is not None and _is_cancellation_drain(fn):
            return None
        return "the results are discarded"

    if isinstance(parent, ast.Assign):
        names = {
            target.id for target in parent.targets if isinstance(target, ast.Name)
        } | {
            element.id
            for target in parent.targets
            if isinstance(target, ast.Tuple)
            for element in target.elts
            if isinstance(element, ast.Name)
        }
        if not names or fn is None:
            return None
        loaded = {
            n.id for n in ast.walk(fn)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
        }
        unread = sorted(names - loaded)
        if unread:
            return f"{', '.join(unread)} is assigned and never read again"
    return None


def test_no_parallel_fanout_discards_its_results():
    """`return_exceptions=True` collects the failures; something must read them.

    `set_zone_eq` gathered one write per zone member, dropped the list and
    returned a hardcoded True, and `apply_zone_equalizer` logged "applied to all
    members" on top of it: a satellite that refused a curve kept the old one
    with nothing in the journal, nothing in the UI banner, and a 200 on the
    route. `_apply_partial_update` had the same hole on the zone branch while
    the single-client branch two lines below let the failure through to a 503.

    A gather that follows a `cancel()` in the same function is exempt: it is a
    drain, and the exceptions it holds back are the cancellations it caused.
    """
    offenders = []
    for path, node, parents in FANOUTS:
        fn = _enclosing(node, parents, _FUNC)
        site = _site(path, node, parents)
        if site in EXEMPT_FANOUTS:
            continue
        reason = _fanout_outcome(node, parents, fn)
        if reason:
            offenders.append(f"{site}:{node.lineno}: {reason}")

    assert not offenders, (
        "parallel fan-out(s) collecting failures nobody can see:\n  "
        + "\n  ".join(sorted(offenders))
        + "\nCount them, name the recipients that failed at error level, and let "
          "the caller answer with the outcome."
    )


# --------------------------------------------------------------------------- #
# Rule 2 — a spawned process must have its exit status or its output read.
# --------------------------------------------------------------------------- #

EXEMPT_SPAWNS: dict[str, str] = {}


def _spawn_binding(spawn, scope):
    """(expression the process object is bound to, its binding statement).

    A `self.<attr>` binding widens the search to the whole class: the BlueALSA
    monitor spawns in `start()`, reads the stream in `_read_output()` and the
    exit code in `stop()`, which is correct and must not read as a hole.
    """
    for node in ast.walk(scope):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        value = node.value.value if isinstance(node.value, ast.Await) else node.value
        if value is not spawn:
            continue
        target = node.targets[0]
        if isinstance(target, (ast.Name, ast.Attribute)):
            return ast.unparse(target), node
    return None, None


def _lifetime(name: str, binding, fn):
    """(first, last) line over which a local `name` still means this process."""
    rebinds = sorted(
        stmt.lineno
        for stmt in ast.walk(fn)
        if isinstance(stmt, ast.Assign)
        and stmt.lineno > binding.lineno
        and any(isinstance(t, ast.Name) and t.id == name for t in stmt.targets)
    )
    return binding.lineno, (rebinds[0] - 1) if rebinds else (fn.end_lineno or binding.lineno)


def _aliases(expression, scope, window=None):
    """`expression` plus the names one call away from it.

    `/api/system/temperature` builds two coroutines, hands both to
    `asyncio.gather` and reads the exit codes off what comes back — one hop, and
    the process object never wears the name it was spawned under.
    """
    found = {expression}
    for node in ast.walk(scope):
        if not (isinstance(node, ast.Assign) and node.value is not None):
            continue
        if not _in_window(node, window):
            continue
        value = node.value.value if isinstance(node.value, ast.Await) else node.value
        if not isinstance(value, ast.Call):
            continue
        if not any(
            ast.unparse(arg) == expression
            for arg in list(value.args) + [kw.value for kw in value.keywords]
        ):
            continue
        for target in node.targets:
            elements = target.elts if isinstance(target, ast.Tuple) else [target]
            found |= {ast.unparse(e) for e in elements if isinstance(e, (ast.Name, ast.Attribute))}
    return found


def _consumes_output(scope, expression, window=None) -> bool:
    """True if the process's output is bound to something, not merely drained.

    `await proc.communicate()` as a statement throws the output away exactly as
    the rollback's two npm steps did; `stdout, _ = await proc.communicate()`
    keeps it, and whatever the caller then parses is its check on the run.
    """
    for node in ast.walk(scope):
        value = None
        for field in ("value", "iter", "context_expr"):
            value = getattr(node, field, None)
            if value is not None:
                break
        if value is None or not isinstance(
            node, (ast.Assign, ast.AnnAssign, ast.For, ast.AsyncFor, ast.Return,
                   ast.comprehension, ast.withitem)
        ):
            continue
        if not _in_window(node, window):
            continue
        for inner in ast.walk(value):
            if (
                isinstance(inner, ast.Attribute)
                and inner.attr in OUTPUT_CHANNELS
                and ast.unparse(inner.value) == expression
            ):
                return True
    return False


def _spawns():
    """(file, call node, parents, enclosing function) for every subprocess spawn."""
    found = []
    for path, tree in _TREES.items():
        parents = _parents(tree)
        seen = set()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in SPAWN_FACTORIES or id(node) in seen:
                continue
            seen.add(id(node))
            found.append((path, node, parents, _enclosing(node, parents, _FUNC)))
    return found


SPAWNS = _spawns()
# A floor, not a count (74 as of 2026-08-20).
assert len(SPAWNS) >= 40, f"only {len(SPAWNS)} subprocess spawns found — extractor broken?"
assert any("milo-client/" in _rel(p) for p, _, _, _ in SPAWNS), (
    "no satellite spawn found — the second tree is not being walked"
)


def _spawn_outcome(path, node, parents, fn):
    """Why this spawn's outcome is unreadable, or None if it is read."""
    if fn is None:
        return "spawned at module level, with nothing to read its outcome"

    expression, binding = _spawn_binding(node, fn)
    if expression is None:
        return "the process object is never bound, so neither exit code nor output can be read"

    # An instance attribute outlives the method that spawned into it, so the
    # whole class is in scope. A local name does not: it is this process only
    # until the next assignment to it.
    if expression.startswith("self."):
        scope, window = _enclosing(node, parents, (ast.ClassDef,)) or fn, None
    else:
        scope, window = fn, _lifetime(expression, binding, fn)

    for alias in _aliases(expression, scope, window):
        if _reads_expression(scope, alias, "returncode", window) or _consumes_output(
            scope, alias, window
        ):
            return None
    return f"neither {expression}.returncode nor its output is ever read"


def test_no_subprocess_outcome_goes_unread():
    """A process that exits non-zero must change something, or it never ran.

    The rollback's `npm install` and `npm run build` drained `communicate()` and
    looked at nothing, so a rollback that could not rebuild the frontend logged
    "completed successfully"; `_screen_cmd` wrote `screen_on` whatever the
    backlight did, so the brightness-apply route reported a level the panel
    never showed; `bluealsa-cli monitor` died on EOF with no log and no exit
    code read, leaving the source "started" and connection detection mute.

    Reading the *output* counts as reading the outcome — `systemctl is-active`
    answers on stdout with a non-zero exit, so its parse is the check. Reading
    neither is the hole: nothing the process did can reach the program.
    """
    offenders = []
    for path, node, parents, fn in SPAWNS:
        site = _site(path, node, parents)
        if site in EXEMPT_SPAWNS:
            continue
        reason = _spawn_outcome(path, node, parents, fn)
        if reason:
            offenders.append(f"{site}:{node.lineno}: {reason}")

    assert not offenders, (
        "subprocess(es) whose outcome nothing can observe:\n  "
        + "\n  ".join(sorted(offenders))
        + "\nCheck `returncode`, log stderr on failure, and let the caller answer "
          "with the result."
    )


# --------------------------------------------------------------------------- #
# Rule 3 — a verdict must not be manufactured over a discarded sibling verdict.
# --------------------------------------------------------------------------- #

# One line per entry, and each must still match something (asserted below).
# Empty since 2026-08-20: the three sites this rule was written against are all
# fixed, so it now holds on merit rather than by exemption.
EXEMPT_MANUFACTURED: dict[str, str] = {}


def _sealed_false(fn) -> bool:
    """True if `fn` carries ``@handle_errors(default=False)``.

    That decorator catches **every** Exception and returns the default, so a
    function wearing it cannot signal failure by raising: its bool is provably
    the only failure channel it has. This is the same "off the exception
    channel" property rules 1 and 2 turn on, and the reason this rule is
    decidable where the general "a bool nobody tested" is not.
    """
    for dec in fn.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        target = dec.func
        name = (
            target.id if isinstance(target, ast.Name)
            else target.attr if isinstance(target, ast.Attribute)
            else None
        )
        if name != "handle_errors":
            continue
        for kw in dec.keywords:
            if (
                kw.arg == "default"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is False
            ):
                return True
    return False


def _owns(fn, node, parents) -> bool:
    """True if `node` belongs to `fn` itself and not to a function nested in it."""
    cursor = parents.get(node)
    while cursor is not None and not isinstance(cursor, _FUNC):
        cursor = parents.get(cursor)
    return cursor is fn or cursor is None


def _manufactures_verdict(fn, parents) -> bool:
    """True if every `return` in `fn` is a bool literal and at least one is True.

    A function that derives its answer — ``return applied``, ``return not
    failed_members(...)`` — is reading something, whatever it reads. One
    whose every exit is a literal has decided its answer before doing the work,
    and the ``True`` branch is the claim this rule is about.
    """
    returns = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.Return) and _owns(fn, node, parents)
    ]
    if not returns:
        return False
    literals = [
        r for r in returns
        if isinstance(r.value, ast.Constant) and isinstance(r.value.value, bool)
    ]
    return len(literals) == len(returns) and any(r.value.value is True for r in literals)


def _discarded_sealed_siblings(fn, parents, sealed: set):
    """`(line, method)` for each `await self.<m>(...)` statement `fn` drops.

    ``self.<m>()`` inside class C binds to ``C.m`` exactly — no dict-based DI to
    see through, no homonym to guess between. That is the whole reason this rule
    exists while the general consumer check does not: the 289-name ambiguity
    documented above never arises for a sibling call.
    """
    found = []
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Expr) and _owns(fn, node, parents)):
            continue
        value = node.value
        if not (isinstance(value, ast.Await) and isinstance(value.value, ast.Call)):
            continue
        func = value.value.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "self"
            and func.attr in sealed
            and func.attr != fn.name  # a sealed method recursing into itself
        ):
            found.append((node.lineno, func.attr))
    return sorted(found)


def _manufactured_verdicts():
    """Every function that claims success over a discarded sealed sibling."""
    found = []
    for path, tree in _TREES.items():
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            sealed = {
                m.name for m in cls.body if isinstance(m, _FUNC) and _sealed_false(m)
            }
            if not sealed:
                continue
            for fn in (m for m in cls.body if isinstance(m, _FUNC)):
                fn_parents = _parents(fn)
                if not _manufactures_verdict(fn, fn_parents):
                    continue
                dropped = _discarded_sealed_siblings(fn, fn_parents, sealed)
                if dropped:
                    found.append((path, fn, dropped))
    return found


MANUFACTURED = _manufactured_verdicts()
# No floor here: unlike the two rules above this one measures an *absence*, and
# an empty result is the goal state rather than a broken parser. The extractor's
# own liveness is pinned by the discriminator test below, which feeds it source
# it must classify both ways.


def test_no_verdict_is_manufactured_over_a_discarded_sibling():
    """A function must not answer True over a sibling's dropped answer.

    The shape this was written against: ``apply_zone_crossover`` called
    ``_set_client_filter`` six times, dropped all six and returned a hardcoded
    True; ``set_zone_crossover_frequency`` dropped *that* and returned True in
    turn; the route tested the bool it got and answered 200 with the new
    frequency. The subwoofer that refused the lowpass kept playing full-range,
    and the only trace was a ``debug`` line inside ``_proxy_filter_to_client`` —
    the exact outcome of occurrence 5.1, on the path 5.1 did not cover. Both are
    fixed; this keeps the shape from coming back.

    The rule is sound because both halves are local. ``@handle_errors(default=
    False)`` proves the callee cannot raise, so its bool is its only failure
    channel; ``self.<m>()`` binds inside the class with no resolver. Neither
    half needs the call graph this file records as unavailable.

    A callee that signals by *raising* is out of scope by construction: the
    decorator would catch it and turn the caller's hardcoded True into False on
    its own, which is why ``_save_data`` and ``bypass_effects`` — sealed
    functions that drop an awaited write — are not flagged.
    """
    offenders = []
    for path, fn, dropped in MANUFACTURED:
        site = f"{_rel(path)}::{fn.name}"
        if site in EXEMPT_MANUFACTURED:
            continue
        calls = ", ".join(f"self.{m}() at line {ln}" for ln, m in dropped)
        offenders.append(f"{site}:{fn.lineno}: returns a literal over {calls}")

    assert not offenders, (
        "verdict(s) manufactured over a discarded sibling verdict:\n  "
        + "\n  ".join(sorted(offenders))
        + "\nCollect what the sibling answered, name what failed at error level, "
          "and return that instead of a literal."
    )


def test_the_manufactured_verdict_extractor_discriminates():
    """Both directions, on the shape the crossover pair actually has.

    ``manufactured`` is ``apply_zone_crossover`` reduced to its skeleton — the
    guard returning False, the loop dropping the sibling, the hardcoded True.
    The four negatives are the shapes that must stay silent: reading the
    sibling's answer, deriving the verdict from it, calling a sibling that is
    *not* sealed (its failure travels by exception, which the decorator turns
    into False for free), and a sealed method recursing into itself.
    """
    source = (
        "class C:\n"
        "    @handle_errors(default=False)\n"
        "    async def _set_client_filter(self, cid, name, on, freq):\n"
        "        return await self._proxy(cid, name, on, freq)\n"
        "    async def _plain_helper(self, cid):\n"
        "        return await self._proxy(cid)\n"
        "    @handle_errors(default=False)\n"
        "    async def manufactured(self, zone_id):\n"
        "        if not self._registry:\n"
        "            return False\n"
        "        for cid in zone_id:\n"
        "            await self._set_client_filter(cid, 'crossover', True, 80)\n"
        "        return True\n"
        "    @handle_errors(default=False)\n"
        "    async def inspected(self, zone_id):\n"
        "        ok = True\n"
        "        for cid in zone_id:\n"
        "            if not await self._set_client_filter(cid, 'crossover', True, 80):\n"
        "                ok = False\n"
        "        return ok\n"
        "    @handle_errors(default=False)\n"
        "    async def derived(self, zone_id):\n"
        "        return await self._set_client_filter(zone_id, 'lowpass', True, 80)\n"
        "    @handle_errors(default=False)\n"
        "    async def drops_an_unsealed_sibling(self, zone_id):\n"
        "        await self._plain_helper(zone_id)\n"
        "        return True\n"
        "    @handle_errors(default=False)\n"
        "    async def _set_client_filter_retry(self, cid):\n"
        "        await self._set_client_filter_retry(cid)\n"
        "        return True\n"
    )
    tree = ast.parse(source)
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
    sealed = {m.name for m in cls.body if isinstance(m, _FUNC) and _sealed_false(m)}
    verdicts = {}
    for fn in (m for m in cls.body if isinstance(m, _FUNC)):
        fn_parents = _parents(fn)
        verdicts[fn.name] = (
            _discarded_sealed_siblings(fn, fn_parents, sealed)
            if _manufactures_verdict(fn, fn_parents) else []
        )

    assert verdicts["manufactured"] == [(12, "_set_client_filter")]
    assert verdicts["inspected"] == []
    assert verdicts["derived"] == []
    assert verdicts["drops_an_unsealed_sibling"] == []
    assert verdicts["_set_client_filter_retry"] == []
    # The sealed set is what makes the negative above meaningful: if nothing
    # parsed as sealed, every case would come back empty for the wrong reason.
    assert sealed == {
        "_set_client_filter", "manufactured", "inspected", "derived",
        "drops_an_unsealed_sibling", "_set_client_filter_retry",
    }


# --------------------------------------------------------------------------- #
# Rules 1 and 2, in both directions, on hand-written source.
# --------------------------------------------------------------------------- #

def _classify(source: str, rule: str):
    """Run one rule over a module written inline.

    Returns {function: [reason|None, ...]} in source order — a list, because one
    function can hold several sites and their verdicts must not overwrite each
    other. That is how the rollback's three `proc` spawns are told apart.
    """
    tree = ast.parse(source)
    parents = _parents(tree)
    out = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        fn = _enclosing(node, parents, _FUNC)
        if rule == "fanout" and node.func.attr == "gather":
            out.setdefault(fn.name, []).append((node.lineno, _fanout_outcome(node, parents, fn)))
        if rule == "spawn" and node.func.attr in SPAWN_FACTORIES:
            out.setdefault(fn.name, []).append(
                (node.lineno, _spawn_outcome(Path(__file__), node, parents, fn))
            )
    # By line, not by walk order: ast.walk is breadth-first and says nothing
    # about which of two sites in one function comes first.
    return {name: [reason for _, reason in sorted(sites)] for name, sites in out.items()}


def test_the_fanout_extractor_discriminates():
    """A guardrail that only ever runs against a clean tree proves nothing.

    Pins that the rule catches both ways of losing the collected outcome, and
    leaves alone the three shapes that keep it: reading the list, handing it to
    a helper, and draining tasks the function itself cancelled.
    """
    verdicts = _classify(
        "import asyncio\n"
        "async def discarded(members):\n"
        "    await asyncio.gather(*[push(m) for m in members], return_exceptions=True)\n"
        "    return True\n"
        "async def assigned_and_forgotten(members):\n"
        "    results = await asyncio.gather(*[push(m) for m in members], return_exceptions=True)\n"
        "    return True\n"
        "async def inspected(members):\n"
        "    results = await asyncio.gather(*[push(m) for m in members], return_exceptions=True)\n"
        "    return not [r for r in results if isinstance(r, BaseException)]\n"
        "async def handed_off(self, members):\n"
        "    results = await asyncio.gather(*[push(m) for m in members], return_exceptions=True)\n"
        "    return not self._failed_members('ctx', members, results)\n"
        "async def drained(self):\n"
        "    for task in self._tasks:\n"
        "        task.cancel()\n"
        "    await asyncio.gather(*self._tasks, return_exceptions=True)\n",
        "fanout",
    )
    assert verdicts["discarded"] == ["the results are discarded"]
    assert verdicts["assigned_and_forgotten"] == ["results is assigned and never read again"]
    assert verdicts["inspected"] == [None]
    assert verdicts["handed_off"] == [None]
    assert verdicts["drained"] == [None]


def test_the_spawn_extractor_discriminates():
    """Same, for the exit status.

    `blind` is `_screen_cmd` as it stood before phase 6 — including the
    `@handle_errors(default=None)` it carried, which is the point: the decorator
    sees exceptions, and an exit code is not one.

    `rebound` is the rollback, whose three spawns shared the name `proc` and
    whose last one checked its exit code. Written without a per-binding window,
    this rule read that one check as covering all three and stayed green when
    the two unchecked npm steps were restored — found by running it against the
    pre-fix code, not by reading it.
    """
    verdicts = _classify(
        "import asyncio\n"
        "class C:\n"
        "    @handle_errors(default=None)\n"
        "    async def blind(self, cmd):\n"
        "        process = await asyncio.create_subprocess_shell(cmd)\n"
        "        await asyncio.wait_for(process.communicate(), 5.0)\n"
        "        self.screen_on = True\n"
        "    async def unbound(self, cmd):\n"
        "        await asyncio.create_subprocess_shell(cmd)\n"
        "    async def rebound(self, cmd):\n"
        "        proc = await asyncio.create_subprocess_exec('npm', 'install')\n"
        "        await proc.communicate()\n"
        "        proc = await asyncio.create_subprocess_exec('pip', 'install')\n"
        "        stdout, stderr = await proc.communicate()\n"
        "        if proc.returncode != 0:\n"
        "            raise Exception(stderr)\n"
        "    async def checked(self, cmd):\n"
        "        process = await asyncio.create_subprocess_shell(cmd)\n"
        "        await process.communicate()\n"
        "        return process.returncode == 0\n"
        "    async def parsed(self, cmd):\n"
        "        process = await asyncio.create_subprocess_shell(cmd)\n"
        "        stdout, _ = await process.communicate()\n"
        "        return stdout.decode().strip() == 'active'\n"
        "    async def start(self):\n"
        "        self._process = await asyncio.create_subprocess_exec('bluealsa-cli')\n"
        "    async def stop(self):\n"
        "        if self._process.returncode is None:\n"
        "            self._process.kill()\n",
        "spawn",
    )
    unread = "neither {}.returncode nor its output is ever read"
    assert verdicts["blind"] == [unread.format("process")]
    # The npm spawn is flagged; the pip spawn two lines below, which reuses the
    # name and does check, is not.
    assert verdicts["rebound"] == [unread.format("proc"), None]
    assert verdicts["unbound"] == [
        "the process object is never bound, so neither exit code nor output can be read"
    ]
    assert verdicts["checked"] == [None]
    assert verdicts["parsed"] == [None]
    assert verdicts["start"] == [None]  # read from a sibling method, across the class


def test_every_exemption_is_still_reached():
    """An exemption for a site that moved silences a rule nobody notices again.

    Each entry names a call site by `file::function`; when that site stops
    existing — renamed, deleted, or fixed — the entry must go with it.
    """
    fanout_sites = {_site(p, n, par) for p, n, par in FANOUTS}
    spawn_sites = {_site(p, n, par) for p, n, par, _ in SPAWNS}
    # Rule 3 collects only the functions that *do* manufacture, so an exemption
    # whose site was fixed drops out of MANUFACTURED and reads as stale here.
    manufactured_sites = {f"{_rel(p)}::{fn.name}" for p, fn, _ in MANUFACTURED}

    stale = sorted(
        [f"EXEMPT_FANOUTS[{k!r}]" for k in EXEMPT_FANOUTS if k not in fanout_sites]
        + [f"EXEMPT_SPAWNS[{k!r}]" for k in EXEMPT_SPAWNS if k not in spawn_sites]
        + [f"EXEMPT_MANUFACTURED[{k!r}]" for k in EXEMPT_MANUFACTURED
           if k not in manufactured_sites]
    )
    assert not stale, f"exemptions matching no call site: {stale} — delete them."

    unexplained = sorted(
        k for k, why in (
            list(EXEMPT_FANOUTS.items())
            + list(EXEMPT_SPAWNS.items())
            + list(EXEMPT_MANUFACTURED.items())
        )
        if not why.strip()
    )
    assert not unexplained, f"exemptions with no reason: {unexplained}"
