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

## Current state (2026-07-27)

**Full backend** (157 modules): 1698 nodes, 9323 edges, **2923 resolved — 31.4 % raw,
39.7 % excluding logging and builtins**; the `api/` + `routes.py` entry-point layer sits
at 38.2 %. `backend/core/multiroom/` stays the fast regression scope: 240 / 1186 / 268,
and those numbers must not move for free.

**Audited: 2923/2923 resolved edges confirmed by the runtime oracle, zero false
positives, zero undecidable.** ~95 % rest on syntactic proof; the rest additionally rest
on a type annotation being truthful (spot-verified by `isinstance` against the real
object graph, 0 false; the remainder needs `initialize_services()`, i.e. starting units
on the appliance — deliberately not done).

A REST-route trace now unfolds through the whole stack (route → closures → service →
state store → registry → DSP routing). Next lever: unannotated service attributes such
as `VolumeService._routing_service`. Details, defects found and levers: PLAN.md.

## Next

Build layer 2 (LLM annotator), then layer 3 (web viewer).
