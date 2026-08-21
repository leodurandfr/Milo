# Codemap — guidance for Claude Code

## Goal

A reusable code-cartography tool: turn a package into a call graph **exact by
construction**, so a feature's real path through the code can be explored visually.
Project-agnostic by design — Milō is only the first subject.

## Architecture — three layers, never mixed

1. **Analyser** — deterministic, zero LLM. Stdlib `ast` → nodes (functions/methods)
   and edges (call sites) with exact file + line. **Built and tested** (`extract_graph.py`).
2. **Annotator** — LLM description per node, cached by function hash, always anchored
   to file + lines so it stays checkable. **Not started.**
3. **Viewer** — generic web page reading the JSON, zoom/pan/click. **Not started.**

## Non-negotiable rule

An edge is `resolved: true` **only** when the callee is proven syntactically to be one
of the extracted nodes. With no proof the call is still emitted, `resolved: false` + a
machine-readable `reason` — never guessed. An incomplete but honest graph beats a
complete one that is sometimes wrong; never trade this for a better resolution rate.
Both scripts fail loud rather than emit a plausible-looking empty result.

## Running it

```bash
python3 tools/codemap/extract_graph.py --target backend --name backend_graph.json
```

`--target` is repeatable/comma-separated and **recursive**; `tests`, `__pycache__`,
`venv`, `node_modules` are skipped (`--include-tests` to keep tests). Writes one graph
JSON (`nodes`, `edges`, `classes` with the inferred attribute-type table, `path_check`)
plus its own `<scope>_REPORT.md` to `tools/codemap/output/` — gitignored, regenerate
rather than commit (~1.8 s for the whole backend).

**After any resolver change, re-run the oracle** — `verify_graph.py` re-checks every
resolved edge with Python's own runtime name resolution (real MRO, real imports,
decorators unwrapped), never with the extractor's logic; `undecidable` is not a pass.
`fixtures/verified_edges.json` freezes the confirmed set as a regression guard.

```bash
venv/bin/python tools/codemap/verify_graph.py --graph tools/codemap/output/backend_graph.json
```

## Current state (2026-08-21, remeasured)

**Full backend** (167 modules): 1915 nodes, 10303 edges, **3325 resolved — 32.3 % raw,
40.6 % excluding logging and builtins**. `backend/core/multiroom/` stays the fast
regression scope: **238 / 1149 / 264**, and those numbers must not move for free.

**Audited: 3325/3325 resolved edges confirmed by the runtime oracle, zero mismatch,
zero undecidable, zero node-table issue** (re-run 2026-08-21 on the current tree).
**2960 (89 %) rest on syntactic proof**; the other **365** rest on a type annotation
being truthful (`declaration — receiver annotated` 349, `alias` 16). That set is the
whole manual audit surface: an annotation states the class, it does not prove what is
injected. Spot-verified by `isinstance` against the real object graph, 0 false; the
remainder needs `initialize_services()`, i.e. starting units on the appliance —
deliberately not done.

A REST-route trace unfolds through the whole stack (route → closures → service → state
store → registry → DSP routing). The blind spot that once killed every trace at depth 1
was the `create_*_router(deps)` factory-closure pattern: the parameters are annotated
under `TYPE_CHECKING` (zero runtime change) and closures resolve through an
enclosing-scope pass. **Next lever, unspent:** a service's own unannotated attributes
(`VolumeService._routing_service`, `self.state_machine`) — same proven lever, one layer
down.

**Two rules the audits paid for — do not soften either.**

- **A mismatch is investigated, never relaxed away.** Building the closure support, the
  oracle flagged 18 then 1 false positives; all 19 were bugs *in the oracle* (the
  parent-calls-own-child shape, then decorated functions whose wrapper code object knows
  nothing of the real body). The extractor was right every time. Loosening the check on
  the first red would have hidden the real defects it did find — 13 doubly-nested
  closures registered twice under a flattened qualname, carrying 57 phantom edges.
- **A resolver pass stops at a nested scope boundary** (`own_nodes()`). `local_types`
  used to descend into nested bodies and leak a closure's locals into its parent's type
  table — a silent false-positive source. Same reason `collect_imports`' module-wide
  table is checked for scope leaks by the oracle (`binding_import` → `mismatch`):
  a function-local import must not resolve a call in another function. 0 occurrences
  in the backend, and it stays checked.

`fixtures/verified_edges.json` is a 2026-07-27 oracle snapshot, regenerated with
`--freeze`, outside the lint floor — leave it alone unless you are deliberately
re-baselining.

## Next

**The build-out is closed (2026-08-21).** Layer 1 is the deliverable and is maintained;
layers 2 (LLM annotator) and 3 (viewer) were designed and never started, and the plan
file that held that roadmap was deleted with this decision. Nothing here is in flight —
if the tool is picked up again, it starts from the levers named above, not from a plan.
