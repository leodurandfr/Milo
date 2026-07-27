# Codemap — work in flight

Companion to `CLAUDE.md`. **CLAUDE.md holds the current, always-true state; this file
holds work in flight and the evidence log.** A fact has exactly one owner: when an item
lands and its conclusion is durable, move the conclusion to CLAUDE.md and strike it
here. This file is deleted when the chantier ends.

Order agreed 2026-07-27: recursive scope → falsification tests → entry points /
reverse index → cross-boundary extractors → layer 2 (text first) → layer 3 (GUI, tree
first).

## Status

| # | Item | State |
|---|---|---|
| 1 | Recursive `--target`, multi-root, output naming | **done** |
| 2 | Test A — false-positive audit | **done — 0 false positives** |
| 2 | Test B — unresolved-edge audit (36 sampled) | **done** |
| 2 | Test C — end-to-end feature trace | **done — fails on the API layer** |
| 2b | Enclosing-scope resolver | **done — +48, all verified** |
| 2c | Annotate the router-factory params | **done — +202, all verified** |
| 3 | Entry-point index + reverse index | todo |
| 4 | Cross-boundary extractors (REST / WS / registry bus) | todo |
| 5 | Layer 2 — annotation store + text digest | todo |
| 6 | Layer 3 — GUI, expandable tree | todo |

## Decisions (do not relitigate)

- **A crossing edge is a contract match, not a call.** It must carry a distinct `kind`
  (`serves`/`consumes`, `emits`/`handles`) so "proven call" and "proven contract" are
  never conflated. Both are exact; they are different claims.
- **Annotations live outside the source files**, keyed by node id + body hash, so a
  changed function flags its annotation `stale` instead of letting it lie. Two fields:
  `does` (observed) and `should` (intent); their divergence is the cleanup backlog.
- **Type annotations are the one place the extractor trusts a human declaration.**
  This is the known soft spot in the "exact by construction" claim — bounded and
  auditable, hence test A weights `typed_receiver` at 100 % coverage.
- **Plan file lives in `tools/codemap/`**, not `docs/plans/`, because the chantier must
  stay self-contained (the tool is meant to move to other projects).

## Evidence log

### Item 1 — recursive scope (2026-07-27)

Verified: rerunning the old scope `--target backend/core/multiroom` reproduces
241 nodes / 1192 edges / 261 resolved and the identical node list. No drift.

Full backend: 157 modules, 1711 nodes, 9380 edges, 2675 resolved (28.5 % / 36.1 %
excluding noise), 1.8 s. **The 48-edge prediction from the annotation pass came out
48/48** — the first end-to-end confirmation that the resolver's "out of scope" reason
means what it claims.

Two facts that feed later items:
- 1711 nodes at backend scope confirms the force-directed hairball risk: layer 3 must
  be a tree/trace explorer, not a canvas.
- Rows like `self.settings_service` resolving 10/27 show the annotation lever is
  repeatable but manual; worth measuring its ceiling before spending more on it.

Fixed in passing: `ClassInfo` now carries its own AST node. The previous
`class_def()` re-walked the module and matched a `ClassDef` **by name**, so two
same-named classes in one module resolved to the first one found — latent wrong-data
bug, invisible at multiroom scale, real at full-backend scale.

### Item 2 — audits (2026-07-27)

**Instrument**: `verify_graph.py`, an independent oracle. The extractor reads syntax;
the oracle asks the *interpreter* (real MRO, real imports, `inspect.unwrap`, identity
compared on `(__module__, __qualname__)` so decorators and `functools.wraps` don't
blur it). Two different methods, so a disagreement means one is wrong. `undecidable`
is never counted as a pass — that rule is what caught the oracle's own first run
being 100 % undecidable because `sys.path` lacked the repo root.

**Test A — false positives: 2675 / 2675 confirmed, 0 mismatch, 0 undecidable**, node
table clean. Frozen to `fixtures/verified_edges.json` as a regression guard.

Breakdown of what each resolution *rests on* (added to the report, carried as edge
data — `receiver_basis` — not sniffed from evidence strings):
- 2537 (94.8 %) proof: `self`, import resolution, in-scope constructor, return annotation.
- 138 (5.2 %) a human type annotation. Of the 23 distinct (class, attribute) pairs
  behind them, **9 verified true by `isinstance` against the app's real object graph,
  0 false**, 14 unverifiable without running `initialize_services()` — deliberately not
  done: that starts systemd/ALSA/D-Bus units on the actual appliance.

Fixed during the audit: `TypeInfo.kind` conflated proof-by-construction
(`self._bg = BackgroundTaskSet(...)`) with trust-by-annotation, both labelled
"declared". Now `constructed | annotated | returned | alias | produced`. Without this
the audit surface read as 367 edges instead of the real 138.

Latent risk checked and cleared: `collect_imports` walks the whole module, so a
function-local import enters the module-wide table and could resolve a call from
another function that cannot see it. **0 occurrences** in the backend; the oracle now
checks it permanently (`binding_import` → verdict `mismatch` on a scope leak).

**Test B — 36 unresolved edges sampled, stratified by reason.** All correctly
unprovable (stdlib/container methods, loggers, builtins, external imports, callbacks
held in attributes, polymorphic hooks) **except two families**, both provable:
- sibling closures called by name inside the same enclosing function — **33 edges**;
- names visible from an enclosing scope generally. `local_types` only reads the node's
  own body and params, never the enclosing function's.

**Test C — end-to-end trace of `PATCH /api/volume/client/{mac}`: the trace dies at
depth 1.** `_mac_from_url`, `_validate_mac_exists`, `_validate_volume_limits` are
sibling closures; `volume_service.update_client_volume_db` hangs off an unannotated
factory parameter. Meanwhile the same tracer on `_handle_client_connect` (plain
methods, annotated class) unfolds correctly.

Root cause, measured: the `create_*_router(deps)` factory-closure pattern. **23
factories, 58 parameters, 5 annotated. 203 unresolved edges** hang off the 53
unannotated ones (`state_machine` 30, `registry_service` 27, `network_service` 15,
`hardware_service` 14, `settings_service` 13, …), plus the 33 sibling-closure edges.

**Conclusion that matters more than the 28.5 % headline: the weakness is not uniform,
it is concentrated in the entry-point layer — exactly where every trace starts.** A
28.5 % graph that resolves services but not routes cannot answer "what happens when
the user does X", which is the tool's whole purpose. Hence items 2b and 2c before
anything else.

### Items 2b + 2c — closing the entry-point blind spot (2026-07-27)

**2b, enclosing-scope resolver.** A closure now sees its enclosing functions' locals and
their nested defs, merged outermost-first so an inner binding shadows an outer one —
the interpreter's own rule. New proof kind `enclosing_scope_function`: **+48 edges**.

Two real defects surfaced while building it, both of which had been quietly wrong:
- `collect_nested` walked to any depth with the *current* prefix, so **13 doubly-nested
  closures were registered twice, one copy under a flattened, wrong qualname** — and
  their call sites were duplicated with them (57 phantom edges). The node-table oracle
  had missed this because it skips `<locals>` nodes by design.
- `local_types` descended into nested bodies, leaking a closure's locals into its
  parent's type table — a latent false-positive source. Both fixed by `own_nodes()`,
  which stops at a nested scope boundary.

The oracle needed extending for closures (unreachable by `getattr`): it now reads the
parent's `co_consts` and checks the caller either defines the callee (`def` binds a
local) or sees it as a cell (`co_freevars`). Its first two versions were themselves
wrong — flagging 18 then 1 false positives that turned out to be oracle bugs (the
parent-calls-own-child shape, then decorated functions returning a wrapper whose code
object knows nothing of the real body). **The extractor was right in all 19 cases**;
the discipline of investigating every mismatch rather than relaxing the check is what
kept that straight.

**2c, factory parameters.** Every router factory has exactly ONE call site
(`backend/main.py`), and each argument there is a `get_service("…")` singleton whose
class is a literal in `dependencies.py` — so all 47 annotations are derived, not
guessed. Applied via `TYPE_CHECKING` (zero runtime change), plus `create_health_router`
by hand (multi-param lines). **+202 edges.**

**Result — the blind spot is closed.** The `PATCH /api/volume/client/{mac}` trace that
died at depth 1 now unfolds through route → validation closures → `VolumeService` →
`VolumeStateStore` → clamp / debounced persist / registry → `EqualizerController` →
`EqualizerRouter` routing decision.

| | before | after |
|---|---|---|
| resolved (backend) | 2675 (28.5 %) | **2923 (31.4 %)**, 39.7 % excluding noise |
| `api/` + `routes.py` layer | — | **38.2 %** |
| oracle verdict | 2675/2675 | **2923/2923 confirmed, 0 mismatch, 0 undecidable** |

Next lever, now visible in the trace: `VolumeService`'s own unannotated attributes
(`self._routing_service`, `self.state_machine`) — same proven lever, service layer.

### Baseline caveat (2026-07-27)

The 2923 figure was measured with `backend/api/multiroom.py` annotated, but that file
also carries an unrelated in-progress feature (`_push_volume_control`), so its
annotations were **left out of the commit** rather than dragging someone else's work
in. Until that file lands, a clean checkout measures **2884** — the 39-edge difference
is `create_multiroom_router`'s four parameters, not a regression. Re-measure and update
the numbers here once it is committed.

## Dead ends

_(nothing yet — record here what was tried and abandoned, with the reason)_
