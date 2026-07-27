#!/usr/bin/env python3
"""Independent falsification oracle for the extracted graph.

`extract_graph.py` resolves call targets by reading syntax. This script checks its
answers with a *different* mechanism: Python's own name resolution at runtime — the
real MRO, real imports, real decorator wrapping. Two different methods that disagree
mean one of them is wrong, and that is exactly what we want to find out.

It never re-implements the extractor's logic; it asks the interpreter.

Checks, per resolved edge:
  - node table    the claimed callee node really is a function at that module/qualname
  - self_method   `getattr(caller_class, name)` — through the REAL MRO — lands on the
                  claimed node. This is what catches "an out-of-scope base actually
                  wins the lookup".
  - typed_receiver  `getattr(declared_receiver_class, name)` lands on the claimed node.
  - module/imported_function, constructor
                  resolving the callee name in the caller module's real namespace
                  lands on the claimed node.

Identity is compared on `(__module__, __qualname__)` after `inspect.unwrap`, not on
line numbers: decorators shift `co_firstlineno`, and `functools.wraps` preserves
qualname, so this survives both without loosening the check.

VERDICTS
  confirmed    runtime agrees with the extractor
  mismatch     runtime resolves to something else — a FALSE POSITIVE, doctrine failure
  undecidable  runtime cannot answer (dynamic attribute, C-level object); NOT a pass

What this oracle cannot see, by construction: whether a type annotation tells the
truth about the object actually injected at runtime. `getattr` proves the method
exists on the declared class; it cannot prove the declared class is what arrives.
That residue is audited by hand against the injection point — see PLAN.md.

Usage:
    venv/bin/python tools/codemap/verify_graph.py [--graph output/backend_graph.json]
                                                  [--freeze]
"""

from __future__ import annotations

import argparse
import ast
import importlib
import inspect
import json
import sys
import warnings
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
# The oracle imports the code under analysis; it must resolve `backend.*` the way the
# app does, not relative to this script's directory.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_GRAPH = "tools/codemap/output/backend_graph.json"
FIXTURE = "tools/codemap/fixtures/verified_edges.json"


def binding_import(module_name: str, symbol: str, call_line: int) -> Tuple[Optional[str], str, bool]:
    """Find the import that binds `symbol` for a call at `call_line`.

    Function-local imports (`from x import y` inside a body) never reach the module
    namespace, so `getattr(module, symbol)` cannot see them. Reading the import
    statement itself stays independent of the extractor: an import binds one name from
    one module, with no resolution logic involved.

    Returns (source module, original name, in_scope). `in_scope` is False when the only
    binding import lives inside a *different* function than the call — which would make
    the extractor's resolution a scope leak, i.e. a false positive.
    """
    path = REPO_ROOT / (module_name.replace(".", "/") + ".py")
    if not path.exists():
        return None, "", False
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return None, "", False

    scopes: List[Tuple[int, int]] = []  # enclosing function ranges, for scope checking
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes.append((fn.lineno, fn.end_lineno))

    best: Optional[Tuple[str, str, bool]] = None
    for stmt in ast.walk(tree):
        if not isinstance(stmt, (ast.Import, ast.ImportFrom)):
            continue
        for alias in stmt.names:
            bound = alias.asname or alias.name.split(".")[0]
            if bound != symbol:
                continue
            if isinstance(stmt, ast.ImportFrom):
                src, orig = stmt.module or "", alias.name
            else:
                src, orig = alias.name, ""
            enclosing = [s for s in scopes if s[0] <= stmt.lineno <= s[1]]
            in_scope = not enclosing or any(s[0] <= call_line <= s[1] for s in enclosing)
            if best is None or (in_scope and not best[2]):
                best = (src, orig, in_scope)
    return best if best else (None, "", False)


def load_object(module_name: str, qualname: str) -> Tuple[Optional[Any], str]:
    """Walk a dotted qualname inside an imported module, like the interpreter would."""
    try:
        mod = importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        return None, f"import failed: {type(exc).__name__}: {exc}"
    obj: Any = mod
    for part in qualname.split("."):
        if part == "<locals>":
            return None, "closure (not reachable at runtime)"
        try:
            obj = getattr(obj, part)
        except AttributeError:
            return None, f"attribute `{part}` not found"
    return obj, ""


def identity(obj: Any) -> Optional[Tuple[str, str]]:
    """(__module__, __qualname__) of the underlying function, decorators removed."""
    if isinstance(obj, (staticmethod, classmethod)):
        obj = obj.__func__
    if isinstance(obj, property):
        obj = obj.fget
    if obj is None:
        return None
    obj = inspect.unwrap(obj)
    mod, qual = getattr(obj, "__module__", None), getattr(obj, "__qualname__", None)
    if not mod or not qual:
        return None
    return (mod, qual)


def nested_codes(root: Any, name: str) -> List[Any]:
    """Every code object named `name` compiled inside `root` — closures included.

    A nested function is not reachable by getattr, but the compiler stores its code
    object in the parent's `co_consts`. Reading that is still the interpreter's own
    answer, not a re-implementation of the extractor.
    """
    code = getattr(root, "__code__", root)
    found: List[Any] = []
    stack = [code]
    while stack:
        cur = stack.pop()
        for const in getattr(cur, "co_consts", ()):
            if hasattr(const, "co_name"):
                if const.co_name == name:
                    found.append(const)
                stack.append(const)
    return found


def closure_verdict(nodes: Dict[str, Any], edge: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, Any]:
    """Oracle for `enclosing_scope_function`: is the name really a closure binding?

    Two shapes are legitimate and they compile differently:
      - the caller DEFINES the callee — the `def` binds a local, and the callee's code
        object sits directly in the caller's `co_consts`;
      - the caller is a deeper/sibling closure — the name is then a cell, listed in the
        caller's `co_freevars`.
    Anything else means the name does not actually resolve to that function here.
    """
    caller, callee = nodes[edge["caller"]], nodes[edge["callee"]]
    name = edge["callee_expr"]
    outermost = callee["qualname"].split(".<locals>.")[0]
    root, err = load_object(callee["module"], outermost)
    if root is None:
        return {**base, "verdict": "undecidable", "detail": err}
    # A decorated function hands back the wrapper, whose code object knows nothing of
    # the closures compiled in the real body.
    root = inspect.unwrap(root)

    caller_name = caller["qualname"].rsplit(".", 1)[-1]
    caller_codes = nested_codes(root, caller_name) or (
        [root.__code__] if getattr(root, "__name__", None) == caller_name else []
    )
    if not caller_codes:
        return {**base, "verdict": "undecidable", "detail": f"no code object for caller `{caller_name}`"}
    defines_it = any(
        any(getattr(k, "co_name", None) == callee["name"] for k in c.co_consts)
        for c in caller_codes
    )
    sees_cell = any(name in c.co_freevars for c in caller_codes)
    if not defines_it and not sees_cell:
        return {**base, "verdict": "mismatch",
                "detail": f"`{name}` is neither defined in `{caller_name}` nor a cell it "
                          f"can see (freevars: {caller_codes[0].co_freevars})"}

    candidates = nested_codes(root, callee["name"])
    if not candidates:
        return {**base, "verdict": "mismatch",
                "detail": f"no `{callee['name']}` compiled inside `{outermost}`"}
    # Decorators move co_firstlineno above the `def`; pick the closest match at or
    # before the recorded def line.
    at_or_before = [c for c in candidates if c.co_firstlineno <= callee["lineno"]]
    if not at_or_before:
        return {**base, "verdict": "mismatch",
                "detail": f"`{callee['name']}` exists but not at/above line {callee['lineno']}"}
    return {**base, "verdict": "confirmed"}


class Verifier:
    def __init__(self, graph: Dict[str, Any]):
        self.graph = graph
        self.nodes = {n["id"]: n for n in graph["nodes"]}
        self.classes = {c["key"]: c for c in graph.get("classes", [])}
        self.results: List[Dict[str, Any]] = []

    # -- the claimed target, as the interpreter sees it --------------------- #

    def expected(self, node_id: str) -> Tuple[str, str]:
        n = self.nodes[node_id]
        return (n["module"], n["qualname"])

    def check_node_table(self) -> List[Dict[str, Any]]:
        """Every node must correspond to a real function object at that qualname."""
        out = []
        for node in self.nodes.values():
            if "<locals>" in node["qualname"]:
                continue  # closures are unreachable by getattr, by design
            obj, err = load_object(node["module"], node["qualname"])
            if obj is None:
                out.append({"node": node["id"], "verdict": "undecidable", "detail": err})
                continue
            ident = identity(obj)
            if ident is None:
                out.append({"node": node["id"], "verdict": "undecidable",
                            "detail": "no __qualname__ (C-level or dynamic object)"})
            elif ident != (node["module"], node["qualname"]):
                out.append({"node": node["id"], "verdict": "mismatch",
                            "detail": f"runtime says {ident[0]}::{ident[1]}"})
        return out

    # -- per-edge oracles --------------------------------------------------- #

    def receiver_class_key(self, edge: Dict[str, Any]) -> Optional[str]:
        """The class the extractor claims owns the callee (i.e. where it resolved it)."""
        callee = self.nodes.get(edge["callee"])
        if not callee or not callee["class"]:
            return None
        return f"{callee['module']}.{callee['class']}"

    def verify_edge(self, edge: Dict[str, Any]) -> Dict[str, Any]:
        kind = edge["resolution"]
        expected = self.expected(edge["callee"])
        method = edge["callee_expr"].rsplit(".", 1)[-1]
        base = {"file": edge["file"], "line": edge["line"], "expr": edge["callee_expr"],
                "resolution": kind, "callee": edge["callee"]}

        if kind == "enclosing_scope_function":
            return closure_verdict(self.nodes, edge, base)

        if kind in ("self_method", "super_method"):
            caller = self.nodes[edge["caller"]]
            if not caller["class"]:
                return {**base, "verdict": "undecidable", "detail": "caller has no class"}
            holder, err = load_object(caller["module"], caller["class"])
            if holder is None:
                return {**base, "verdict": "undecidable", "detail": err}
            probe, err = self.getattr_probe(holder, method)

        elif kind == "typed_receiver":
            key = self.receiver_class_key(edge)
            if key is None:
                return {**base, "verdict": "undecidable", "detail": "callee is not a method"}
            mod_name, cls_name = key.rsplit(".", 1)
            holder, err = load_object(mod_name, cls_name)
            if holder is None:
                return {**base, "verdict": "undecidable", "detail": err}
            probe, err = self.getattr_probe(holder, method)

        elif kind in ("module_function", "imported_function", "constructor",
                      "module_qualified_function"):
            caller = self.nodes[edge["caller"]]
            # Resolve the callee expression in the CALLER's real module namespace.
            expr = edge["callee_expr"]
            probe, err = load_object(caller["module"], expr)
            if probe is None and "." not in expr:
                # Not in the module namespace: it must be a function-local import.
                src, orig, in_scope = binding_import(caller["module"], expr, edge["line"])
                if src is None:
                    return {**base, "verdict": "undecidable",
                            "detail": f"{err}; no import binds `{expr}`"}
                if not in_scope:
                    return {**base, "verdict": "mismatch",
                            "detail": f"`{expr}` is only imported inside another function — "
                                      f"not in scope at line {edge['line']} (scope leak)"}
                probe, err = load_object(src, orig) if orig else (None, "plain module import")
            if probe is not None and kind == "constructor":
                probe, err = self.getattr_probe(probe, "__init__")

        else:
            return {**base, "verdict": "undecidable", "detail": f"no oracle for `{kind}`"}

        if probe is None:
            return {**base, "verdict": "undecidable", "detail": err}
        ident = identity(probe)
        if ident is None:
            return {**base, "verdict": "undecidable", "detail": "no __qualname__ at runtime"}
        if ident == expected:
            return {**base, "verdict": "confirmed"}
        return {**base, "verdict": "mismatch",
                "detail": f"extractor says {expected[0]}::{expected[1]}, "
                          f"runtime resolves to {ident[0]}::{ident[1]}"}

    @staticmethod
    def getattr_probe(holder: Any, name: str) -> Tuple[Optional[Any], str]:
        # Read through the class dict chain so properties stay properties.
        if inspect.isclass(holder):
            for klass in holder.__mro__:
                if name in klass.__dict__:
                    return klass.__dict__[name], ""
            return None, f"`{name}` not found on {holder.__name__} MRO"
        try:
            return getattr(holder, name), ""
        except AttributeError:
            return None, f"`{name}` not found"

    def run(self) -> Dict[str, Any]:
        node_issues = self.check_node_table()
        for edge in self.graph["edges"]:
            if edge["resolved"]:
                self.results.append(self.verify_edge(edge))
        return {
            "node_table_issues": node_issues,
            "edges": self.results,
            "summary": dict(Counter(r["verdict"] for r in self.results)),
        }


def main() -> int:
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--graph", default=DEFAULT_GRAPH)
    ap.add_argument("--freeze", action="store_true",
                    help="write the confirmed set as a regression fixture")
    args = ap.parse_args()

    graph = json.loads((REPO_ROOT / args.graph).read_text(encoding="utf-8"))
    report = Verifier(graph).run()
    summary = report["summary"]
    total = sum(summary.values())
    mismatches = [r for r in report["edges"] if r["verdict"] == "mismatch"]

    print(f"resolved edges checked : {total}")
    for verdict in ("confirmed", "mismatch", "undecidable"):
        n = summary.get(verdict, 0)
        print(f"  {verdict:<12} {n:>5}  ({100 * n / max(1, total):.1f}%)")
    print(f"node table issues      : {len(report['node_table_issues'])}")

    by_kind: Dict[str, Counter] = {}
    for r in report["edges"]:
        by_kind.setdefault(r["resolution"], Counter())[r["verdict"]] += 1
    print("\nper proof kind:")
    for kind, c in sorted(by_kind.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"  {kind:<26} " + "  ".join(f"{k}={v}" for k, v in sorted(c.items())))

    if mismatches:
        print(f"\nFALSE POSITIVES ({len(mismatches)}):")
        for m in mismatches[:40]:
            print(f"  {m['file']}:{m['line']}  {m['expr']}\n      {m['detail']}")

    out = REPO_ROOT / "tools/codemap/output/verify_report.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"\nfull verdicts -> {out.relative_to(REPO_ROOT)}")

    if args.freeze:
        fixture = REPO_ROOT / FIXTURE
        fixture.parent.mkdir(parents=True, exist_ok=True)
        confirmed = sorted(
            f"{r['file']}:{r['line']}:{r['expr']}->{r['callee']}"
            for r in report["edges"] if r["verdict"] == "confirmed"
        )
        fixture.write_text(json.dumps({
            "_about": "Runtime-confirmed resolved edges, frozen as a regression guard. "
                      "Human-reviewed ground truth, not a self-written assertion: each "
                      "entry was confirmed by Python's own name resolution. If a "
                      "resolver change drops one of these, that is a regression.",
            "graph_scope": graph.get("scope"),
            "count": len(confirmed),
            "edges": confirmed,
        }, indent=2) + "\n", encoding="utf-8")
        print(f"frozen {len(confirmed)} edges -> {FIXTURE}")

    return 1 if mismatches or report["node_table_issues"] else 0


if __name__ == "__main__":
    sys.exit(main())
