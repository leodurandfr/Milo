#!/usr/bin/env python3
"""Exact (non-heuristic) call-graph extractor built on the stdlib `ast` module.

Scope of this prototype: a single package directory (default
`backend/core/multiroom/`). Every node is a function or method actually defined
in a parsed file; every edge is a call site actually present in a node's body.

Resolution doctrine — an edge is `resolved: true` only when the callee can be
proven to be one of the extracted nodes from syntax alone (a self-method, an
attribute whose class is pinned by an annotation / constructor call / annotated
parameter, a module-level function reachable through an in-scope import). Every
other call site is still emitted, with `resolved: false` and a machine-readable
`reason`. No guessing: an unprovable target is never attached to a node.

Known, deliberate approximations (they only ever *lose* resolutions, never
invent them):
  - Flow-insensitive: local variable types are collected over the whole body,
    and a name bound twice to conflicting types becomes ambiguous (unresolved).
  - Lambdas are not nodes: calls inside a lambda are attributed to the enclosing
    function, so a handler table `{"X": lambda p: self._handle(p)}` still yields
    the edge.
  - Decorator expressions are not walked (they are not the body).
  - Module-level calls belong to no node; they are counted, not emitted.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import json
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TARGET = "backend/core/multiroom"
DEFAULT_OUTPUT = "tools/codemap/output"
DEFAULT_EXCLUDES = frozenset({"__pycache__", ".git", "venv", ".venv", "node_modules", "tests"})

# Files whose unresolved edges are enumerated in full in the report.
REPORT_UNRESOLVED_FOR = ("websocket.py", "client_registry.py")

# Path submitted for manual verification, as consecutive (caller, callee) links.
PATH_CHECK: List[Tuple[str, str]] = [
    ("_handle_client_connect", "_register_snapclient"),
    ("_register_snapclient", "register_client"),
    ("register_client", "get_reconnection_context"),
    ("get_reconnection_context", "_resolve_target_volume"),
    ("_resolve_target_volume", "_apply_target_volume_to_client"),
    ("_apply_target_volume_to_client", "set_client_online"),
]

AMBIGUOUS = "<ambiguous>"
BUILTIN_NAMES = frozenset(dir(builtins))


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

@dataclass
class FuncNode:
    id: str
    qualname: str
    name: str
    file: str
    module: str
    cls: Optional[str]
    lineno: int
    end_lineno: int
    is_async: bool
    is_property: bool
    decorators: List[str]

    def to_json(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "qualname": self.qualname,
            "name": self.name,
            "file": self.file,
            "module": self.module,
            "class": self.cls,
            "lineno": self.lineno,
            "end_lineno": self.end_lineno,
            "is_async": self.is_async,
            "is_property": self.is_property,
            "decorators": self.decorators,
        }


@dataclass
class TypeInfo:
    """A proven-or-not type binding for an attribute / local name.

    `kind` records HOW the binding is known, because the strength of the claim differs:

      constructed  `self.x = SomeClass(...)` — proof. The class is right there.
      annotated    an annotation names the class — a *human declaration*. The only
                   place the extractor trusts a human; a wrong annotation is the one
                   way a resolved edge can be wrong.
      returned     the callee's return annotation — a declaration, one hop removed.
      alias        inherited from the attribute a property returns; keeps its source's kind.
      produced     only the producing expression is known (`x = foo.bar()`), not the
                   class. Never resolves; kept to explain why.
    """
    type_name: str                    # dotted source spelling, or AMBIGUOUS
    class_key: Optional[str]          # "<module>.<Class>" when in scope
    evidence: str
    kind: str = "annotated"

    @property
    def in_scope(self) -> bool:
        return self.class_key is not None


@dataclass
class ClassInfo:
    key: str
    name: str
    module: str
    file: str
    lineno: int
    bases: List[str]
    node: Any = None                  # the ClassDef itself — never re-find it by name

    methods: Dict[str, FuncNode] = field(default_factory=dict)
    attrs: Dict[str, TypeInfo] = field(default_factory=dict)
    properties: Dict[str, str] = field(default_factory=dict)  # prop name -> aliased self attr


@dataclass
class ModuleInfo:
    module: str
    file: str
    tree: ast.Module
    classes: Dict[str, ClassInfo] = field(default_factory=dict)
    functions: Dict[str, FuncNode] = field(default_factory=dict)
    # imported symbol name -> "<module>.<symbol>" (in-scope) or raw dotted path
    imports: Dict[str, str] = field(default_factory=dict)
    # imported module alias -> module dotted path
    module_imports: Dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Small AST helpers
# --------------------------------------------------------------------------- #

def dotted_name(node: ast.AST) -> Optional[str]:
    """`a.b.c` -> "a.b.c" for Name/Attribute chains only."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted_name(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def unwrap_annotation(node: Optional[ast.AST]) -> Optional[str]:
    """Return the dotted class name an annotation pins, or None.

    Unwraps `Optional[X]` / `Union[X, None]` and string forward refs. A
    container annotation (`Dict[str, X]`, `List[X]`) pins no instance type and
    returns None — the call target would be a container method, not X's.
    """
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return unwrap_annotation(ast.parse(node.value, mode="eval").body)
        except SyntaxError:
            return None
    if isinstance(node, (ast.Name, ast.Attribute)):
        return dotted_name(node)
    if isinstance(node, ast.Subscript):
        head = dotted_name(node.value)
        if head is None:
            return None
        if head.split(".")[-1] not in ("Optional", "Union"):
            return None
        sl = node.slice
        elts = sl.elts if isinstance(sl, ast.Tuple) else [sl]
        candidates = []
        for e in elts:
            if isinstance(e, ast.Constant) and e.value is None:
                continue
            name = unwrap_annotation(e)
            if name:
                candidates.append(name)
        if len(candidates) == 1:
            return candidates[0]
        return None
    return None


def decorator_names(fn: ast.AST) -> List[str]:
    out = []
    for d in getattr(fn, "decorator_list", []):
        target = d.func if isinstance(d, ast.Call) else d
        name = dotted_name(target)
        if name:
            out.append(name)
    return out


def body_statements(fn: ast.AST) -> List[ast.stmt]:
    return list(getattr(fn, "body", []))


def own_nodes(fn: ast.AST):
    """Walk a function body WITHOUT entering nested def/class bodies.

    Those are separate scopes: their statements are not this function's, and their
    nested defs are not its direct children. Walking into them flattens the scope
    tree — which registered doubly-nested closures under a wrong qualname, and leaked
    their locals into the parent's type table.
    """
    def walk(node: ast.AST):
        for child in ast.iter_child_nodes(node):
            yield child
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            yield from walk(child)

    for stmt in body_statements(fn):
        yield stmt
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield from walk(stmt)


# --------------------------------------------------------------------------- #
# Pass 1 — parse, collect nodes, classes, imports
# --------------------------------------------------------------------------- #

class Extractor:
    def __init__(self, repo_root: Path, targets: List[Path], excludes: Set[str]):
        self.repo_root = repo_root
        self.targets = targets
        self.excludes = excludes
        self.modules: Dict[str, ModuleInfo] = {}
        self.classes: Dict[str, ClassInfo] = {}          # "<module>.<Class>"
        self.nodes: Dict[str, FuncNode] = {}             # node id -> node
        self.node_owner_body: Dict[str, ast.AST] = {}    # node id -> ast def
        self.node_context: Dict[str, Tuple[str, Optional[str]]] = {}  # id -> (module, class key)
        self.scope_children: Dict[str, Dict[str, FuncNode]] = {}      # node id -> defs it contains
        self.edges: List[Dict[str, Any]] = []
        self.module_level_calls = 0
        self.stats: Dict[str, Any] = {}

    # -- parsing ----------------------------------------------------------- #

    def module_name(self, path: Path) -> str:
        rel = path.relative_to(self.repo_root).with_suffix("")
        return ".".join(rel.parts)

    def rel(self, path: Path) -> str:
        return str(path.relative_to(self.repo_root))

    def discover(self) -> List[Path]:
        """Every .py under the targets, recursively, minus excluded directories."""
        seen: Set[Path] = set()
        for root in self.targets:
            if root.is_file():
                seen.add(root)
                continue
            for path in root.rglob("*.py"):
                rel_parts = path.relative_to(self.repo_root).parts
                if any(part in self.excludes for part in rel_parts):
                    continue
                seen.add(path)
        return sorted(seen)

    def parse_all(self) -> None:
        files = self.discover()
        if not files:
            raise SystemExit(
                f"FATAL: no .py file found under {', '.join(str(t) for t in self.targets)}"
            )
        for path in files:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            mod = ModuleInfo(module=self.module_name(path), file=self.rel(path), tree=tree)
            self.modules[mod.module] = mod
            self.collect_imports(mod)
            self.collect_defs(mod)

    def collect_imports(self, mod: ModuleInfo) -> None:
        for node in ast.walk(mod.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod.module_imports[alias.asname or alias.name.split(".")[0]] = alias.name
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import
                    base = mod.module.rsplit(".", node.level)[0]
                    src = f"{base}.{node.module}" if node.module else base
                else:
                    src = node.module or ""
                for alias in node.names:
                    local = alias.asname or alias.name
                    mod.imports[local] = f"{src}.{alias.name}"
                    mod.module_imports.setdefault(local, src)

    def collect_defs(self, mod: ModuleInfo) -> None:
        for stmt in mod.tree.body:
            if isinstance(stmt, ast.ClassDef):
                self.collect_class(mod, stmt)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node = self.make_node(mod, stmt, cls=None, prefix="")
                mod.functions[node.name] = node
                self.collect_nested(mod, stmt, node.id, node.qualname, cls=None)

    def collect_class(self, mod: ModuleInfo, cls_def: ast.ClassDef) -> None:
        key = f"{mod.module}.{cls_def.name}"
        info = ClassInfo(
            key=key,
            name=cls_def.name,
            module=mod.module,
            file=mod.file,
            lineno=cls_def.lineno,
            bases=[b for b in (dotted_name(x) for x in cls_def.bases) if b],
            node=cls_def,
        )
        mod.classes[cls_def.name] = info
        self.classes[key] = info
        for stmt in cls_def.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node = self.make_node(mod, stmt, cls=cls_def.name, prefix=f"{cls_def.name}.")
                info.methods[node.name] = node
                self.collect_nested(mod, stmt, node.id, node.qualname, cls=cls_def.name)
            elif isinstance(stmt, ast.ClassDef):
                self.collect_class(mod, stmt)  # nested class: flat key, name-shadowing accepted

    def collect_nested(self, mod: ModuleInfo, fn: ast.AST, parent_id: str,
                       qual_prefix: str, cls: Optional[str]) -> None:
        """Register the closures defined directly in this body, then recurse one level."""
        for sub in own_nodes(fn):
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node = self.make_node(mod, sub, cls=cls, prefix=f"{qual_prefix}.<locals>.")
                self.scope_children.setdefault(parent_id, {})[node.name] = node
                self.collect_nested(mod, sub, node.id, node.qualname, cls)

    def make_node(self, mod: ModuleInfo, fn: ast.AST, cls: Optional[str], prefix: str) -> FuncNode:
        decos = decorator_names(fn)
        qualname = f"{prefix}{fn.name}"
        node = FuncNode(
            id=f"{mod.module}::{qualname}",
            qualname=qualname,
            name=fn.name,
            file=mod.file,
            module=mod.module,
            cls=cls,
            lineno=fn.lineno,
            end_lineno=fn.end_lineno,
            is_async=isinstance(fn, ast.AsyncFunctionDef),
            is_property=any(d.split(".")[-1] in ("property", "cached_property") for d in decos),
            decorators=decos,
        )
        self.nodes[node.id] = node
        self.node_owner_body[node.id] = fn
        self.node_context[node.id] = (mod.module, f"{mod.module}.{cls}" if cls else None)
        return node

    # -- pass 2: class attribute types ------------------------------------- #

    def resolve_class_name(self, mod: ModuleInfo, name: Optional[str]) -> Optional[str]:
        """Map a source-level type spelling to an in-scope class key, or None."""
        if not name:
            return None
        if name in mod.classes:
            return mod.classes[name].key
        if name in mod.imports:
            target = mod.imports[name]
            if target in self.classes:
                return target
        if "." in name:  # e.g. `models.Client` through an in-scope module import
            head, _, tail = name.rpartition(".")
            src = mod.module_imports.get(head)
            if src and f"{src}.{tail}" in self.classes:
                return f"{src}.{tail}"
        return None

    def bind(self, table: Dict[str, TypeInfo], key: str, info: Optional[TypeInfo]) -> None:
        if info is None:
            return
        prev = table.get(key)
        if prev is None:
            table[key] = info
            return
        if prev.type_name == info.type_name:
            return
        table[key] = TypeInfo(AMBIGUOUS, None, f"{prev.evidence} / {info.evidence}")

    def build_class_attrs(self) -> None:
        for mod in self.modules.values():
            for cls in mod.classes.values():
                self.scan_class_attrs(mod, cls)
        self.resolve_properties()

    def scan_class_attrs(self, mod: ModuleInfo, cls: ClassInfo) -> None:
        for method in cls.methods.values():
            fn = self.node_owner_body[method.id]
            params = self.param_annotations(mod, fn)
            for stmt in ast.walk(fn):
                # self.x: T = ...
                if isinstance(stmt, ast.AnnAssign) and self.is_self_attr(stmt.target):
                    attr = stmt.target.attr
                    spelling = unwrap_annotation(stmt.annotation)
                    if spelling:
                        self.bind(cls.attrs, attr, TypeInfo(
                            spelling,
                            self.resolve_class_name(mod, spelling),
                            f"annotation `self.{attr}: {spelling}` at {mod.file}:{stmt.lineno}",
                        ))
                # self.x = <expr>
                elif isinstance(stmt, ast.Assign):
                    for tgt in stmt.targets:
                        if not self.is_self_attr(tgt):
                            continue
                        attr = tgt.attr
                        info = self.static_expr_type(mod, stmt.value, params, f"{mod.file}:{stmt.lineno}")
                        self.bind(cls.attrs, attr, info)
        # class-level annotations, read off this class's own ClassDef
        for stmt in getattr(cls.node, "body", []):
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                spelling = unwrap_annotation(stmt.annotation)
                if spelling:
                    self.bind(cls.attrs, stmt.target.id, TypeInfo(
                        spelling,
                        self.resolve_class_name(mod, spelling),
                        f"class annotation `{stmt.target.id}: {spelling}` at {mod.file}:{stmt.lineno}",
                    ))
        # property aliases: `@property def x(self): return self._y`
        for method in cls.methods.values():
            if not method.is_property:
                continue
            fn = self.node_owner_body[method.id]
            stmts = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
            if len(stmts) == 1 and isinstance(stmts[0], ast.Return) and self.is_self_attr(stmts[0].value):
                cls.properties[method.name] = stmts[0].value.attr
            elif len(stmts) == 1 and isinstance(stmts[0], ast.Return):
                ann = unwrap_annotation(getattr(fn, "returns", None))
                if ann:
                    self.bind(cls.attrs, method.name, TypeInfo(
                        ann,
                        self.resolve_class_name(mod, ann),
                        f"property return annotation `{method.name}() -> {ann}` at {mod.file}:{fn.lineno}",
                    ))

    def resolve_properties(self) -> None:
        """Fixpoint: a property aliasing `self._x` inherits `_x`'s type."""
        for _ in range(4):
            changed = False
            for cls in self.classes.values():
                for prop, attr in cls.properties.items():
                    src = cls.attrs.get(attr)
                    if src is None or prop in cls.attrs:
                        continue
                    cls.attrs[prop] = TypeInfo(
                        src.type_name, src.class_key,
                        f"property `{prop}` -> `self.{attr}`; {src.evidence}",
                        "alias" if src.kind == "annotated" else src.kind,
                    )
                    changed = True
            if not changed:
                break

    @staticmethod
    def is_self_attr(node: Optional[ast.AST]) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        )

    def param_annotations(self, mod: ModuleInfo, fn: ast.AST) -> Dict[str, TypeInfo]:
        out: Dict[str, TypeInfo] = {}
        args = getattr(fn, "args", None)
        if args is None:
            return out
        all_args = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
        for arg in all_args:
            spelling = unwrap_annotation(arg.annotation)
            if not spelling:
                continue
            out[arg.arg] = TypeInfo(
                spelling,
                self.resolve_class_name(mod, spelling),
                f"parameter annotation `{arg.arg}: {spelling}` at {mod.file}:{fn.lineno}",
            )
        return out

    def static_expr_type(
        self, mod: ModuleInfo, expr: ast.AST, params: Dict[str, TypeInfo], where: str
    ) -> Optional[TypeInfo]:
        """Type of a *constructing* expression: `X()`, an annotated param, or await of a typed call."""
        if isinstance(expr, ast.Await):
            return self.static_expr_type(mod, expr.value, params, where)
        if isinstance(expr, ast.Call):
            spelling = dotted_name(expr.func)
            if not spelling:
                return None
            key = self.resolve_class_name(mod, spelling)
            if key:
                return TypeInfo(spelling, key, f"constructor `{spelling}(...)` at {where}",
                                "constructed")
            ret = self.call_return_type(mod, expr, params)
            if ret:
                return ret
            # Out of scope: the class is unknown, only the producing expression is.
            return TypeInfo(spelling, None, f"value produced by `{spelling}(...)` at {where}", "produced")
        if isinstance(expr, ast.Name) and expr.id in params:
            p = params[expr.id]
            return TypeInfo(p.type_name, p.class_key, f"{p.evidence} (assigned at {where})", p.kind)
        return None

    def call_return_type(
        self, mod: ModuleInfo, call: ast.Call, params: Dict[str, TypeInfo]
    ) -> Optional[TypeInfo]:
        """Return annotation of an in-scope callee, when it pins an in-scope class."""
        target = self.try_resolve_callee(mod, None, call.func, params, {})
        if not target or not target[0]:
            return None
        node_id = target[0]
        fn = self.node_owner_body.get(node_id)
        spelling = unwrap_annotation(getattr(fn, "returns", None)) if fn else None
        if not spelling:
            return None
        owner_mod = self.modules[self.nodes[node_id].module]
        key = self.resolve_class_name(owner_mod, spelling)
        if not key:
            return None
        return TypeInfo(spelling, key,
                        f"return annotation of `{self.nodes[node_id].qualname}` -> {spelling}",
                        "returned")

    # -- pass 3: locals + callee resolution -------------------------------- #

    def enclosing_chain(self, node_id: str) -> List[str]:
        """Enclosing function node ids, outermost first — a closure's visible scopes."""
        module, _, qualname = node_id.partition("::")
        chain: List[str] = []
        while ".<locals>." in qualname:
            qualname = qualname.rsplit(".<locals>.", 1)[0]
            candidate = f"{module}::{qualname}"
            if candidate in self.nodes:
                chain.append(candidate)
        return list(reversed(chain))

    def visible_scope(
        self, node_id: str, mod: ModuleInfo, cls: Optional[ClassInfo]
    ) -> Tuple[Dict[str, TypeInfo], Dict[str, FuncNode]]:
        """Names a closure can see: enclosing locals + sibling/own nested defs.

        Python resolves a free name in a nested function against its enclosing
        function's scope, so these bindings are as provable as the node's own. Merged
        outermost-first so an inner binding shadows an outer one, exactly like the
        interpreter.
        """
        types: Dict[str, TypeInfo] = {}
        funcs: Dict[str, FuncNode] = {}
        for enclosing in self.enclosing_chain(node_id):
            funcs.update(self.scope_children.get(enclosing, {}))
            types.update(self.local_types(mod, self.node_owner_body[enclosing], cls))
        funcs.update(self.scope_children.get(node_id, {}))
        return types, funcs

    def local_types(self, mod: ModuleInfo, fn: ast.AST, cls: Optional[ClassInfo]) -> Dict[str, TypeInfo]:
        table = dict(self.param_annotations(mod, fn))
        for sub in own_nodes(fn):
            if isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                spelling = unwrap_annotation(sub.annotation)
                if spelling:
                    self.bind(table, sub.target.id, TypeInfo(
                        spelling, self.resolve_class_name(mod, spelling),
                        f"local annotation `{sub.target.id}: {spelling}` at {mod.file}:{sub.lineno}",
                    ))
            elif isinstance(sub, ast.Assign):
                for tgt in sub.targets:
                    if not isinstance(tgt, ast.Name):
                        continue
                    info = self.static_expr_type(mod, sub.value, table, f"{mod.file}:{sub.lineno}")
                    if info is None:
                        info = self.attr_chain_type(mod, cls, sub.value, table)
                    self.bind(table, tgt.id, info)
        return table

    def attr_chain_type(
        self, mod: ModuleInfo, cls: Optional[ClassInfo], expr: ast.AST,
        locals_: Dict[str, TypeInfo], depth: int = 0,
    ) -> Optional[TypeInfo]:
        """Type of `self.a.b` / `local.a` chains, walking proven attribute maps."""
        if depth > 6:
            return None
        if isinstance(expr, ast.Await):
            return self.attr_chain_type(mod, cls, expr.value, locals_, depth + 1)
        if isinstance(expr, ast.Name):
            info = locals_.get(expr.id)
            return info if info and info.type_name != AMBIGUOUS else None
        if isinstance(expr, ast.Attribute):
            if self.is_self_attr(expr):
                if cls is None:
                    return None
                info = self.lookup_attr(cls, expr.attr)
                return info if info and info.type_name != AMBIGUOUS else None
            base = self.attr_chain_type(mod, cls, expr.value, locals_, depth + 1)
            if base and base.class_key:
                owner = self.classes[base.class_key]
                info = self.lookup_attr(owner, expr.attr)
                return info if info and info.type_name != AMBIGUOUS else None
            return None
        return None

    def lookup_attr(self, cls: ClassInfo, attr: str) -> Optional[TypeInfo]:
        for owner in self.mro(cls):
            if attr in owner.attrs:
                return owner.attrs[attr]
        return None

    def mro(self, cls: ClassInfo) -> List[ClassInfo]:
        seen: List[ClassInfo] = []
        queue = deque([cls])
        visited: Set[str] = set()
        while queue:
            cur = queue.popleft()
            if cur.key in visited:
                continue
            visited.add(cur.key)
            seen.append(cur)
            mod = self.modules[cur.module]
            for base in cur.bases:
                key = self.resolve_class_name(mod, base)
                if key and key in self.classes:
                    queue.append(self.classes[key])
        return seen

    def find_method(self, cls: ClassInfo, name: str) -> Optional[FuncNode]:
        for owner in self.mro(cls):
            if name in owner.methods:
                return owner.methods[name]
        return None

    def has_external_base(self, cls: ClassInfo) -> bool:
        for owner in self.mro(cls):
            mod = self.modules[owner.module]
            for base in owner.bases:
                if base in ("object",):
                    continue
                if not self.resolve_class_name(mod, base):
                    return True
        return False

    def try_resolve_callee(
        self, mod: ModuleInfo, cls: Optional[ClassInfo], func: ast.AST,
        locals_: Dict[str, TypeInfo], _memo: Dict[str, Any],
        scope_fns: Optional[Dict[str, FuncNode]] = None,
    ) -> Optional[Tuple[Optional[str], str, str]]:
        """(node_id | None, resolution kind, evidence) — None when nothing applies."""
        # bare name: closure in an enclosing scope, module function, import, constructor
        if isinstance(func, ast.Name):
            name = func.id
            if scope_fns and name in scope_fns:
                n = scope_fns[name]
                return (n.id, "enclosing_scope_function",
                        f"closure `{n.qualname}` visible from the enclosing scope")
            if name in mod.functions:
                n = mod.functions[name]
                return (n.id, "module_function", f"module-level `{name}` at {n.file}:{n.lineno}")
            if name in mod.imports:
                target = mod.imports[name]
                owner_mod, _, sym = target.rpartition(".")
                m = self.modules.get(owner_mod)
                if m and sym in m.functions:
                    n = m.functions[sym]
                    return (n.id, "imported_function", f"import `{target}`")
                if target in self.classes:
                    ctor = self.classes[target].methods.get("__init__")
                    if ctor:
                        return (ctor.id, "constructor", f"import `{target}` -> __init__")
                    return (None, "constructor_no_init", f"class `{target}` defines no __init__")
            if name in mod.classes:
                ctor = mod.classes[name].methods.get("__init__")
                if ctor:
                    return (ctor.id, "constructor", f"local class `{name}` -> __init__")
                return (None, "constructor_no_init", f"class `{name}` defines no __init__")
            return None

        if not isinstance(func, ast.Attribute):
            return None

        base, attr = func.value, func.attr

        # super().m()
        if (
            isinstance(base, ast.Call) and isinstance(base.func, ast.Name)
            and base.func.id == "super" and cls is not None
        ):
            for owner in self.mro(cls)[1:]:
                if attr in owner.methods:
                    n = owner.methods[attr]
                    return (n.id, "super_method", f"super() -> {owner.key}.{attr}")
            return (None, "super_unresolved", "super() base not in scope")

        # self.m()
        if isinstance(base, ast.Name) and base.id == "self":
            if cls is None:
                return None
            m = self.find_method(cls, attr)
            if m:
                return (m.id, "self_method", f"method of {cls.key} (or in-scope base)")
            info = self.lookup_attr(cls, attr)
            if info and info.type_name == AMBIGUOUS:
                return (None, "ambiguous_attr_type", info.evidence)
            if info:
                return (None, "self_attr_is_not_a_method", f"`self.{attr}` typed {info.type_name}")
            if self.has_external_base(cls):
                return (None, "maybe_inherited_from_external_base", f"{cls.key} has an out-of-scope base")
            return (None, "self_member_not_a_method", f"no method `{attr}` on {cls.key}")

        # <module>.func()
        mod_alias = dotted_name(base)
        if mod_alias and mod_alias in mod.module_imports:
            src = mod.module_imports[mod_alias]
            m = self.modules.get(src)
            if m and attr in m.functions:
                n = m.functions[attr]
                return (n.id, "module_qualified_function", f"`{mod_alias}` -> {src}")
            if m and attr in m.classes:
                ctor = m.classes[attr].methods.get("__init__")
                if ctor:
                    return (ctor.id, "constructor", f"`{mod_alias}.{attr}` -> __init__")

        # typed receiver: self.x.m(), local.m(), self.a.b.m()
        recv = self.attr_chain_type(mod, cls, base, locals_)
        if recv is None:
            return None
        if recv.class_key is None:
            if recv.kind == "produced":
                return (None, "receiver_value_out_of_scope",
                        f"receiver is the result of `{recv.type_name}(...)`; {recv.evidence}")
            return (None, "receiver_type_out_of_scope",
                    f"receiver declared `{recv.type_name}`; {recv.evidence}")
        owner = self.classes[recv.class_key]
        m = self.find_method(owner, attr)
        if m:
            return (m.id, "typed_receiver", f"receiver `{recv.type_name}`; {recv.evidence}")
        if self.has_external_base(owner):
            return (None, "method_not_found_external_base", f"{owner.key} has an out-of-scope base")
        return (None, "method_not_found_on_type", f"no `{attr}` on {owner.key}")

    # -- pass 4: edges ------------------------------------------------------ #

    def build_edges(self) -> None:
        for node_id, fn in self.node_owner_body.items():
            mod_name, cls_key = self.node_context[node_id]
            mod = self.modules[mod_name]
            cls = self.classes.get(cls_key) if cls_key else None
            outer_types, scope_fns = self.visible_scope(node_id, mod, cls)
            locals_ = {**outer_types, **self.local_types(mod, fn, cls)}
            awaited = {id(n.value) for n in ast.walk(fn) if isinstance(n, ast.Await)}
            for call in self.own_calls(fn):
                self.emit_edge(node_id, mod, cls, locals_, call, id(call) in awaited, scope_fns)
        self.count_module_level_calls()

    def own_calls(self, fn: ast.AST) -> List[ast.Call]:
        """Calls in this node's body, excluding nested def bodies and decorators."""
        found: List[ast.Call] = []

        def walk(node: ast.AST) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue  # its own node
                if isinstance(child, ast.Call):
                    found.append(child)
                walk(child)

        for stmt in body_statements(fn):
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(stmt, ast.Call):
                found.append(stmt)
            walk(stmt)
        return sorted(found, key=lambda c: (c.lineno, c.col_offset))

    def emit_edge(
        self, caller_id: str, mod: ModuleInfo, cls: Optional[ClassInfo],
        locals_: Dict[str, TypeInfo], call: ast.Call, is_await: bool,
        scope_fns: Optional[Dict[str, FuncNode]] = None,
    ) -> None:
        expr = dotted_name(call.func)
        if expr is None:
            self.edges.append(self.edge_dict(
                caller_id, ast.unparse(call.func), None, call, is_await, False, None,
                "dynamic_callee_expression", "callee is not a plain name/attribute chain",
            ))
            return

        callee_name = expr[len("self."):] if expr.startswith("self.") else expr
        result = self.try_resolve_callee(mod, cls, call.func, locals_, {}, scope_fns)
        if result is None:
            reason, evidence = self.unresolved_reason(mod, cls, call.func, locals_, expr)
            self.edges.append(self.edge_dict(
                caller_id, expr, callee_name, call, is_await, False, None, reason, evidence))
            return

        node_id, kind, evidence = result
        if node_id:
            edge = self.edge_dict(
                caller_id, expr, callee_name, call, is_await, True, node_id, None, evidence, kind)
            if kind == "typed_receiver":
                # Record what the receiver's type rests on: a constructor (proof) or an
                # annotation (a human declaration). This is the audit surface, so it is
                # carried as data, never re-derived from the evidence string.
                recv = self.attr_chain_type(mod, cls, call.func.value, locals_)
                edge["receiver_basis"] = recv.kind if recv else "unknown"
            self.edges.append(edge)
        else:
            self.edges.append(self.edge_dict(
                caller_id, expr, callee_name, call, is_await, False, None, kind, evidence))

    def unresolved_reason(
        self, mod: ModuleInfo, cls: Optional[ClassInfo], func: ast.AST,
        locals_: Dict[str, TypeInfo], expr: str,
    ) -> Tuple[str, str]:
        if isinstance(func, ast.Name):
            if func.id in mod.imports:
                return "external_import", f"imported from `{mod.imports[func.id]}` (outside scope)"
            if func.id in locals_:
                return "local_callable", f"local name declared `{locals_[func.id].type_name}`"
            if func.id in BUILTIN_NAMES:
                return "builtin", f"python builtin `{func.id}`"
            return "unknown_name", "not a module function, in-scope import, class or builtin"
        if isinstance(func, ast.Attribute):
            base = func.value
            if isinstance(base, (ast.Call, ast.Subscript, ast.Await)):
                return "dynamic_receiver", f"receiver is a {type(base).__name__} result"
            head = dotted_name(base)
            if head and head.split(".")[0] in mod.module_imports:
                src = mod.module_imports[head.split(".")[0]]
                return "external_module_attr", f"`{head}` -> `{src}` (outside scope)"
            return "unknown_receiver_type", f"no proven type for `{ast.unparse(base)}`"
        return "unsupported_callee_form", type(func).__name__

    def edge_dict(
        self, caller: str, expr: str, callee_name: Optional[str], call: ast.Call,
        is_await: bool, resolved: bool, callee: Optional[str], reason: Optional[str],
        evidence: str, kind: Optional[str] = None,
    ) -> Dict[str, Any]:
        node = self.nodes[caller]
        return {
            "caller": caller,
            "caller_qualname": node.qualname,
            "file": node.file,
            "line": call.lineno,
            "col": call.col_offset,
            "callee_expr": expr,
            "callee_name": callee_name if callee_name is not None else expr,
            "is_await": is_await,
            "resolved": resolved,
            "callee": callee,
            "resolution": kind,
            "reason": reason,
            "evidence": evidence,
        }

    def count_module_level_calls(self) -> None:
        total = 0
        for mod in self.modules.values():
            for stmt in mod.tree.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                total += sum(1 for n in ast.walk(stmt) if isinstance(n, ast.Call))
        self.module_level_calls = total

    # -- self-check --------------------------------------------------------- #

    def self_check(self) -> None:
        """Fail loud rather than emit a plausible-looking empty surface."""
        problems = []
        if len(self.nodes) < 50:
            problems.append(f"only {len(self.nodes)} nodes extracted")
        if len(self.edges) < 200:
            problems.append(f"only {len(self.edges)} edges extracted")
        resolved = sum(1 for e in self.edges if e["resolved"])
        if resolved < 50:
            problems.append(f"only {resolved} resolved edges")
        if not self.classes:
            problems.append("no class extracted")
        dangling = [e for e in self.edges if e["resolved"] and e["callee"] not in self.nodes]
        if dangling:
            problems.append(f"{len(dangling)} resolved edges point outside the node set")
        for e in self.edges:
            if e["resolved"] == (e["reason"] is not None):
                problems.append(f"edge at {e['file']}:{e['line']} has inconsistent resolved/reason")
                break
        if problems:
            raise SystemExit("FATAL extractor self-check failed: " + "; ".join(problems))


# --------------------------------------------------------------------------- #
# Path verification
# --------------------------------------------------------------------------- #

def verify_path(ex: Extractor) -> List[Dict[str, Any]]:
    by_name: Dict[str, List[FuncNode]] = {}
    for node in ex.nodes.values():
        by_name.setdefault(node.name, []).append(node)

    adjacency: Dict[str, Set[str]] = {}
    for e in ex.edges:
        if e["resolved"]:
            adjacency.setdefault(e["caller"], set()).add(e["callee"])

    reverse: Dict[str, Set[str]] = {}
    for src, dsts in adjacency.items():
        for d in dsts:
            reverse.setdefault(d, set()).add(src)

    def ancestors(ids: Set[str]) -> Dict[str, int]:
        """node id -> distance from the nearest member of `ids`, walking callers."""
        dist = {i: 0 for i in ids}
        queue = deque(ids)
        while queue:
            cur = queue.popleft()
            for prev in reverse.get(cur, ()):
                if prev not in dist:
                    dist[prev] = dist[cur] + 1
                    queue.append(prev)
        return dist

    def shortest_path(src_ids: List[str], dst_ids: Set[str]) -> Optional[List[str]]:
        queue = deque([[s] for s in src_ids])
        seen = set(src_ids)
        while queue:
            path = queue.popleft()
            for nxt in adjacency.get(path[-1], ()):
                if nxt in seen:
                    continue
                new = path + [nxt]
                if nxt in dst_ids:
                    return new
                seen.add(nxt)
                queue.append(new)
        return None

    results = []
    for caller_name, callee_name in PATH_CHECK:
        callers = by_name.get(caller_name, [])
        callees = by_name.get(callee_name, [])
        entry: Dict[str, Any] = {
            "link": f"{caller_name} -> {callee_name}",
            "caller_nodes": [n.id for n in callers],
            "callee_nodes": [n.id for n in callees],
            "direct_edges": [],
            "status": "MISSING",
            "indirect_path": None,
            "common_orchestrator": None,
        }
        callee_ids = {n.id for n in callees}
        for e in ex.edges:
            if e["caller"] not in {n.id for n in callers}:
                continue
            hits_by_id = e["resolved"] and e["callee"] in callee_ids
            hits_by_name = e["callee_name"].split(".")[-1] == callee_name
            if hits_by_id or hits_by_name:
                entry["direct_edges"].append({
                    "file": e["file"], "line": e["line"], "callee_expr": e["callee_expr"],
                    "resolved": e["resolved"], "callee": e["callee"],
                    "resolution": e["resolution"], "reason": e["reason"],
                })
        if any(d["resolved"] for d in entry["direct_edges"]):
            entry["status"] = "RESOLVED"
        elif entry["direct_edges"]:
            entry["status"] = "FOUND_UNRESOLVED"
        elif callers and callees:
            caller_ids = {n.id for n in callers}
            path = shortest_path(list(caller_ids), callee_ids)
            if path:
                entry["status"] = "INDIRECT"
                entry["indirect_path"] = path
            else:
                # No directed path either way: the two may still be consecutive
                # steps of one orchestrator. Report the nearest common caller.
                a, b = ancestors(caller_ids), ancestors(callee_ids)
                common = [(a[k] + b[k], k) for k in a.keys() & b.keys()]
                if common:
                    _, best = min(common)
                    entry["status"] = "SIBLING_STEPS"
                    entry["common_orchestrator"] = {
                        "node": best,
                        "path_to_caller": shortest_path([best], caller_ids) or [best],
                        "path_to_callee": shortest_path([best], callee_ids) or [best],
                    }
        results.append(entry)
    return results


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

def write_report(
    ex: Extractor, path_results: List[Dict[str, Any]], out: Path,
    target_rel: str, graph_name: str,
) -> None:
    edges, nodes = ex.edges, ex.nodes
    resolved = [e for e in edges if e["resolved"]]
    unresolved = [e for e in edges if not e["resolved"]]

    by_reason: Dict[str, int] = {}
    for e in unresolved:
        by_reason[e["reason"]] = by_reason.get(e["reason"], 0) + 1
    by_kind: Dict[str, int] = {}
    for e in resolved:
        by_kind[e["resolution"]] = by_kind.get(e["resolution"], 0) + 1

    per_file: Dict[str, Dict[str, int]] = {}
    for n in nodes.values():
        per_file.setdefault(n.file, {"nodes": 0, "edges": 0, "resolved": 0})["nodes"] += 1
    for e in edges:
        row = per_file.setdefault(e["file"], {"nodes": 0, "edges": 0, "resolved": 0})
        row["edges"] += 1
        row["resolved"] += 1 if e["resolved"] else 0

    L: List[str] = []
    L.append("# Call-graph extraction report")
    L.append("")
    L.append(f"- Scope: `{target_rel}` — {len(ex.modules)} modules scanned recursively")
    L.append("- Extractor: `tools/codemap/extract_graph.py` (stdlib `ast`, no LLM, no heuristic naming)")
    L.append(f"- Graph: `{DEFAULT_OUTPUT}/{graph_name}`")
    L.append("")
    L.append("## Totals")
    L.append("")
    L.append("| Metric | Count |")
    L.append("|---|---|")
    L.append(f"| Nodes (functions + methods) | {len(nodes)} |")
    L.append(f"| — async | {sum(1 for n in nodes.values() if n.is_async)} |")
    L.append(f"| — methods | {sum(1 for n in nodes.values() if n.cls)} |")
    L.append(f"| — module-level functions | {sum(1 for n in nodes.values() if not n.cls)} |")
    L.append(f"| Classes | {len(ex.classes)} |")
    L.append(f"| Edges (call sites) | {len(edges)} |")
    L.append(f"| — resolved | {len(resolved)} ({100 * len(resolved) / max(1, len(edges)):.1f}%) |")
    L.append(f"| — unresolved | {len(unresolved)} ({100 * len(unresolved) / max(1, len(edges)):.1f}%) |")
    L.append(f"| Module-level calls (outside any node, not emitted) | {ex.module_level_calls} |")
    L.append("")
    # Logging and builtins can never resolve to a node in scope; they dominate the
    # raw ratio without saying anything about the graph's usefulness.
    noise = [
        e for e in edges
        if e["reason"] == "builtin" or e["callee_expr"].rsplit(".", 1)[0].split(".")[-1] in ("logger", "log")
    ]
    signal = len(edges) - len(noise)
    L.append(f"Excluding logging calls and builtins ({len(noise)} edges, none of which can point to a")
    L.append(f"node in scope): {len(resolved)} / {signal} call sites resolved "
             f"({100 * len(resolved) / max(1, signal):.1f}%).")
    L.append("")
    L.append("## Resolved edges by proof kind")
    L.append("")
    L.append("| Kind | Count |")
    L.append("|---|---|")
    for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1]):
        L.append(f"| `{k}` | {v} |")
    L.append("")
    # A resolution can rest on proof (self, import, constructor) or on a human
    # declaration (a type annotation). Only the second can be wrong without any code
    # being wrong, so it is the audit surface — it must be visible, not averaged in.
    PROOF = {"constructed", "returned"}
    trust: Dict[str, int] = {}
    for e in resolved:
        if e["resolution"] != "typed_receiver":
            label = f"proof — {e['resolution']}"
        elif e.get("receiver_basis") in PROOF:
            label = f"proof — receiver {e['receiver_basis']}"
        else:
            label = f"declaration — receiver {e.get('receiver_basis', 'unknown')}"
        trust[label] = trust.get(label, 0) + 1
    L.append("## What each resolution rests on")
    L.append("")
    L.append("| Basis | Count |")
    L.append("|---|---|")
    for k, v in sorted(trust.items(), key=lambda kv: -kv[1]):
        L.append(f"| {k} | {v} |")
    L.append("")
    L.append("A *declaration* is the only kind that can be wrong while the code is right:")
    L.append("an annotation states the class, it does not prove what is injected. That set")
    L.append("is the manual audit surface; everything else is proven from syntax.")
    L.append("")
    L.append("## Unresolved edges by reason")
    L.append("")
    L.append("| Reason | Count |")
    L.append("|---|---|")
    for k, v in sorted(by_reason.items(), key=lambda kv: -kv[1]):
        L.append(f"| `{k}` | {v} |")
    L.append("")
    L.append("## Per file")
    L.append("")
    L.append("| File | Nodes | Edges | Resolved | Unresolved |")
    L.append("|---|---|---|---|---|")
    for f, row in sorted(per_file.items()):
        L.append(f"| `{f}` | {row['nodes']} | {row['edges']} | {row['resolved']} | {row['edges'] - row['resolved']} |")
    L.append("")

    receivers: Dict[str, int] = {}
    for e in unresolved:
        if e["reason"] in ("unknown_receiver_type", "receiver_type_out_of_scope",
                           "receiver_value_out_of_scope"):
            head = e["callee_expr"].rsplit(".", 1)[0]
            receivers[head] = receivers.get(head, 0) + 1
    L.append("## Top unresolved receivers")
    L.append("")
    L.append("What the graph would need in order to close: each row is an expression whose class")
    L.append("could not be proven, and the number of call sites that fact costs.")
    L.append("")
    L.append("| Receiver | Unresolved call sites |")
    L.append("|---|---|")
    for k, v in sorted(receivers.items(), key=lambda kv: (-kv[1], kv[0]))[:25]:
        L.append(f"| `{k}` | {v} |")
    L.append("")

    for fname in REPORT_UNRESOLVED_FOR:
        sel = sorted(
            (e for e in unresolved if e["file"].endswith("/" + fname)),
            key=lambda e: (e["line"], e["col"]),
        )
        L.append(f"## Unresolved edges — `{fname}` (complete list: {len(sel)})")
        L.append("")
        L.append("| Line | Caller | Call | Reason | Evidence |")
        L.append("|---|---|---|---|---|")
        for e in sel:
            L.append(
                f"| {e['line']} | `{e['caller_qualname']}` | `{e['callee_expr']}` | "
                f"`{e['reason']}` | {e['evidence']} |"
            )
        L.append("")

    L.append("## Path verification")
    L.append("")
    L.append("Requested chain, link by link. `RESOLVED` = a direct call site exists **and** its")
    L.append("target was proven to be an extracted node. `FOUND_UNRESOLVED` = the call site exists")
    L.append("but the target could not be proven. `INDIRECT` = no direct call site; the shortest")
    L.append("path through resolved edges is given. `SIBLING_STEPS` = neither calls the other, but")
    L.append("both are reached from one common orchestrator (given below). `MISSING` = no relation.")
    L.append("")
    L.append("| # | Link | Status | Site | Resolution / reason |")
    L.append("|---|---|---|---|---|")
    for i, r in enumerate(path_results, 1):
        if r["direct_edges"]:
            d = r["direct_edges"][0]
            site = f"`{d['file'].split('/')[-1]}:{d['line']}` `{d['callee_expr']}`"
            detail = f"`{d['resolution'] or d['reason']}`"
        else:
            site = "—"
            detail = "no direct call site"
        L.append(f"| {i} | `{r['link']}` | **{r['status']}** | {site} | {detail} |")
    L.append("")
    for i, r in enumerate(path_results, 1):
        if not (len(r["direct_edges"]) > 1 or r["indirect_path"] or r["common_orchestrator"]):
            continue
        L.append(f"### Link {i}: `{r['link']}`")
        L.append("")
        for d in r["direct_edges"]:
            L.append(f"- call site `{d['file']}:{d['line']}` — `{d['callee_expr']}` "
                     f"→ resolved={d['resolved']} ({d['resolution'] or d['reason']})")
        if r["indirect_path"]:
            L.append("- shortest resolved path:")
            for step in r["indirect_path"]:
                L.append(f"  - `{step}`")
        if r["common_orchestrator"]:
            co = r["common_orchestrator"]
            L.append(f"- common orchestrator: `{co['node']}`")
            L.append(f"  - to `{r['link'].split(' -> ')[0]}`: " + " → ".join(f"`{s}`" for s in co["path_to_caller"]))
            L.append(f"  - to `{r['link'].split(' -> ')[1]}`: " + " → ".join(f"`{s}`" for s in co["path_to_callee"]))
        L.append("")

    L.append("## Extractor limits (apply to every number above)")
    L.append("")
    L.append("- Flow-insensitive locals: a name bound to two different types is reported ambiguous, never guessed.")
    L.append("- `receiver_type_out_of_scope` = an annotation names a class outside the scope;")
    L.append("  `receiver_value_out_of_scope` = only the producing expression is known (`x = foo.bar()`),")
    L.append("  the value's class is never inferred.")
    L.append("- Method lookup walks in-scope bases only (BFS). A same-named method on an out-of-scope")
    L.append("  base is not considered, and such classes are flagged in the reason instead of resolved.")
    L.append("- Calls inside a `lambda` are attributed to the enclosing function (lambdas are not nodes).")
    L.append("- Decorator expressions are not walked; module-level calls are counted, not emitted as edges.")
    L.append("- A call is only resolved to a node **inside the scanned scope**; a proven call into e.g.")
    L.append("  `backend/shared/` is reported unresolved (`receiver_type_out_of_scope` / `external_import`),")
    L.append("  because its target is not an extracted node.")
    L.append("")
    out.write_text("\n".join(L) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", action="append", default=None,
                    help="directory or file, repo-relative; repeatable or comma-separated. "
                         f"Scanned recursively. Default: {DEFAULT_TARGET}")
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--name", default=None,
                    help="basename of the graph file (default: derived from the first target)")
    ap.add_argument("--exclude", action="append", default=None,
                    help=f"directory name to skip anywhere in the tree (default: {sorted(DEFAULT_EXCLUDES)})")
    ap.add_argument("--include-tests", action="store_true",
                    help="do not skip `tests` directories")
    args = ap.parse_args()

    raw_targets = args.target or [DEFAULT_TARGET]
    rel_targets = [t.strip() for spec in raw_targets for t in spec.split(",") if t.strip()]
    targets = [(REPO_ROOT / t).resolve() for t in rel_targets]
    for t, rel in zip(targets, rel_targets):
        if not t.exists():
            raise SystemExit(f"FATAL: target does not exist: {rel}")

    excludes = set(args.exclude) if args.exclude else set(DEFAULT_EXCLUDES)
    if args.include_tests:
        excludes.discard("tests")

    scope = ", ".join(rel_targets)
    name = args.name or (Path(rel_targets[0]).name + "_graph.json")
    outdir = (REPO_ROOT / args.output).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    ex = Extractor(REPO_ROOT, targets, excludes)
    ex.parse_all()
    ex.build_class_attrs()
    ex.build_edges()
    ex.self_check()

    path_results = verify_path(ex)

    graph = {
        "scope": scope,
        "files_scanned": len(ex.modules),
        "generator": "tools/codemap/extract_graph.py",
        "nodes": [n.to_json() for n in sorted(ex.nodes.values(), key=lambda n: (n.file, n.lineno))],
        "edges": sorted(ex.edges, key=lambda e: (e["file"], e["line"], e["col"])),
        "classes": [
            {
                "key": c.key, "name": c.name, "file": c.file, "lineno": c.lineno,
                "bases": c.bases,
                "attribute_types": {
                    a: {"type": t.type_name, "kind": t.kind, "in_scope": t.in_scope,
                        "class_key": t.class_key, "evidence": t.evidence}
                    for a, t in sorted(c.attrs.items())
                },
            }
            for c in sorted(ex.classes.values(), key=lambda c: (c.file, c.lineno))
        ],
        "module_level_calls": ex.module_level_calls,
        "path_check": path_results,
    }
    (outdir / name).write_text(
        json.dumps(graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    # One report per scope — a shared REPORT.md would let the last run silently
    # overwrite the evidence of the previous one.
    report_name = name[: -len("_graph.json")] if name.endswith("_graph.json") else Path(name).stem
    write_report(ex, path_results, outdir / f"{report_name}_REPORT.md", scope, name)

    resolved = sum(1 for e in ex.edges if e["resolved"])
    print(f"files={len(ex.modules)} nodes={len(ex.nodes)} edges={len(ex.edges)} "
          f"resolved={resolved} unresolved={len(ex.edges) - resolved}")
    for r in path_results:
        print(f"  {r['status']:<18} {r['link']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
