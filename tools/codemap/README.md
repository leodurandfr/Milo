# Codemap

A code-cartography tool: it turns a codebase into a **call graph that is exact by
construction**, so a feature's real path through the code can be read instead of
reconstructed from memory and grep. Project-agnostic by design — Milō is the first
subject, not the target.

For the current numbers and how to run it, see [CLAUDE.md](CLAUDE.md). For what is
being built next and why, see [PLAN.md](PLAN.md). This file is the front door: what the
tool *is*, and what it lets you ask.

## The one rule

An edge is `resolved: true` **only** when the callee is proven, from syntax alone, to be
one of the extracted nodes. With no proof the call is still emitted — `resolved: false`
plus a machine-readable reason. Never guessed, never approximated.

That is the whole value. An incomplete but honest graph can be leaned on: you can delete
code against it, answer a question without re-reading, and let an agent say "this
touches these twelve sites" without anyone checking behind it. A graph that is
occasionally wrong is a pretty diagram. **Never trade the rule for a better resolution
rate.**

It is enforced, not promised: `verify_graph.py` re-derives every resolved edge from
Python's own runtime name resolution and must come back 100 % confirmed, zero mismatch,
zero undecidable, before any change to the resolver is considered done.

## Three layers, never mixed

1. **Analyser** — deterministic, zero LLM. Stdlib `ast` in, nodes and edges out.
   *Built and audited.*
2. **Annotator** — an LLM description per node (`does` = observed, `should` = intent),
   keyed by node id + body hash so a changed function flags its annotation stale rather
   than letting it lie. *Not started.*
3. **Viewer** — a generic page that reads the JSON and unfolds it as an explorable tree.
   Knows nothing about Milō. *Not started.*

Keeping them separate is what makes layer 1's guarantee meaningful: the interpretation
lives one layer up and can never contaminate the structure.

## What ships today

| File | Role |
|---|---|
| `extract_graph.py` | The analyser. Recursive, multi-root, ~2 s for a 157-module backend. |
| `verify_graph.py` | The independent oracle. Falsifies the analyser using the interpreter. |
| `fixtures/verified_edges.json` | The confirmed edge set, frozen as a regression guard. |
| `output/` | Generated graphs and reports. Gitignored — rebuild, don't commit. |

## The data

**Nodes** — one per function/method, including closures. Carries `id`, `qualname`,
`file`, `module`, `class`, exact `lineno`/`end_lineno`, `is_async`, `is_property`,
`decorators`.

**Edges** — one per call site actually present in a body. Carries `caller`, `callee`,
`callee_expr`, `file`/`line`/`col`, `is_await`, `resolved`, and either a `resolution`
(how it was proven) or a `reason` (why it could not be), always with human-readable
`evidence`.

**Proof kinds** — `self_method`, `typed_receiver`, `module_function`,
`imported_function`, `constructor`, `enclosing_scope_function`. Only `typed_receiver`
can rest on a human declaration rather than pure syntax, so those edges also carry
`receiver_basis` (`constructed` and `returned` are proof; `annotated` and `alias` are a
declaration). That field is what lets you ask how much of the graph depends on someone
having written a correct annotation.

**Unresolved reasons** — `unknown_receiver_type`, `receiver_value_out_of_scope`,
`receiver_type_out_of_scope`, `builtin`, `external_module_attr`, `external_import`,
`dynamic_callee_expression`, `constructor_no_init`, `local_callable`, `unknown_name`,
`self_member_not_a_method`, `self_attr_is_not_a_method`,
`method_not_found_external_base`, `maybe_inherited_from_external_base`.

The unresolved set is not failure — it is the honest description of what a static
reading cannot decide, and it is itemised so it can be *worked*: each reason names a
specific missing resolver or a genuinely dynamic hop.

## What it lets you ask

- **"What happens when the user does X?"** Unfold the trace from an entry point. A REST
  route currently unfolds through route → validation closures → service → state store →
  registry → DSP routing.
- **"If I change or delete this, what breaks?"** The reverse index. This matters here:
  a route with no caller in `frontend/src/` is *not* necessarily dead — Milo-Mac is a
  second consumer outside the repo, and one of its pinned WS events reads no payload
  field at all, so it looks unreferenced from every angle.
- **"Where is this concept implemented, and how many times?"** N distinct paths
  converging on the same external effect are visible in a graph and invisible to grep.
- **"I'm coming back to this after six months — where do I enter?"** The entry-point
  index: everything that can start something, then unfold what matters.

And for an agent working on the codebase: a scoped subgraph plus its annotations
replaces reading thousands of lines, and turns "this affects these call sites" from an
inference into a verified fact.

## What it does not do

It is a **static** graph. It does not know which branch runs, how often, or with what
data. A large share of call sites stay unresolved — mostly logging, builtins and
container methods, which can never point into the analysed scope, but also real dynamic
dispatch. And it does not replace reading the code: it tells you **what** to read.
