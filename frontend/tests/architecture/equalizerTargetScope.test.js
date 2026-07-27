// frontend/tests/architecture/equalizerTargetScope.test.js
/**
 * Structural guardrail over equalizerStore's target scoping — the concept its
 * `git log` says keeps breaking (5 of 9 `fix` commits: "make EQ enabled/preset
 * target-aware", "stop flashing local preset name when selecting a remote
 * client", "flash of flat EQ", "skip zone routing", "zone_enabled_changed").
 *
 * The page shows exactly one target at a time — the local DAC, a satellite, or a
 * zone — and every piece of state it renders belongs to that target. Two ways to
 * get that wrong recurred:
 *
 *   1. A WS handler adopts a payload without asking which target it describes.
 *      `handleEnabledChanged` did, and equalizer/enabled_changed is the *local*
 *      bypass with no target field, so a satellite or a zone was reported as
 *      bypassed when it was not.
 *   2. A second reader of the per-target record appears beside loadStatus(),
 *      diverges from it, and the two disagree about what is on screen.
 *      loadEnabledState() was that reader: it re-fetched the whole record to
 *      take one field loadStatus() had just set from the same response.
 *   3. A second representation of "which target is selected" appears. The tab
 *      strip held one, in its own `zone:<mac1>,<mac2>` grammar against the API's
 *      `zone:<zoneId>`, and the two could never compare equal.
 *
 * These rules are STRUCTURAL on purpose. A behavioural test only covers the
 * handler someone thought to write one for; these fail on the *next* handler
 * added, because they know which refs are target-scoped and nothing about which
 * function is supposed to write them.
 *
 * Known limit, stated rather than papered over: rule 2 asks whether a handler has
 * *any* notion of its target, not whether an existing check actually gates. Delete
 * `handleEqualizerChanged`'s `if (!isRelevant) return;` and it still passes, since
 * `target_id` remains in the destructure. Proving that would need dataflow; the
 * gating is covered behaviourally instead (equalizerStore.test.js applies/ignores
 * a change × client/zone, both red on that mutation). Structure catches the
 * missing concept, behaviour catches the wrong comparison.
 *
 * Every extraction asserts it found a plausible surface first — a broken parse
 * must fail loudly, not pass on an empty set.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve, relative } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_DIR = resolve(HERE, '../../src');
const EQ_STORE = join(SRC_DIR, 'stores/equalizerStore.js');
const PARAMETRIC_EQ = join(SRC_DIR, 'components/equalizer/ParametricEQ.vue');
const EQ_BAND = join(SRC_DIR, 'components/equalizer/EQBand.vue');

/** Anything that builds, tests or takes apart the `zone:<id>` API token. */
const ZONE_TOKEN = /(?:`zone:\$\{|['"]zone:)/;

function sourceFiles(dir) {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    return statSync(full).isDirectory()
      ? sourceFiles(full)
      : /\.(js|vue)$/.test(full) ? [full] : [];
  });
}

/**
 * State that describes the selected target, and must therefore never be written
 * from a payload whose target was not checked. `state` (the CamillaDSP daemon's
 * own connection state) and `outputPeak` (an aggregate the caller chooses the
 * clients for) are deliberately absent — they are not per-target.
 */
const TARGET_SCOPED_REFS = [
  'filters', 'compressor', 'loudness', 'mono',
  'activePreset', 'customGains', 'isPresetEdited', 'originalPresetGains',
  'isEqualizerEffectsEnabled',
];

/** Anything that establishes which target a payload or a read is about. */
const SCOPE_CHECKS = [
  'targetRef(', 'getSelectedZoneId(', 'getZoneForClient(',
  'target_id', 'zone_id', 'selectedTarget',
];

const source = readFileSync(EQ_STORE, 'utf8');

/**
 * Split the store into its top-level functions. Pinia setup stores declare them
 * at two-space indentation, so the next such declaration ends the previous body.
 */
function topLevelFunctions() {
  const starts = [...source.matchAll(/^ {2}(?:async )?function (\w+)\s*\(/gm)];
  if (starts.length < 15) {
    throw new Error(`only ${starts.length} functions parsed out of equalizerStore — the extractor is broken`);
  }
  return starts.map((m, i) => ({
    name: m[1],
    body: source.slice(m.index, i + 1 < starts.length ? starts[i + 1].index : source.length),
  }));
}

/** Comments are stripped: a rule must read code, never the prose above it. */
function stripComments(body) {
  return body.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
}

const FUNCTIONS = topLevelFunctions();
const WS_HANDLERS = FUNCTIONS.filter(f => /^handle[A-Z]/.test(f.name));

describe('equalizerStore target scoping', () => {
  it('parsed a plausible surface', () => {
    // Guards every rule below: an empty handler list would make them vacuous.
    expect(WS_HANDLERS.length).toBeGreaterThanOrEqual(4);
    expect(FUNCTIONS.map(f => f.name)).toContain('loadStatus');
    expect(FUNCTIONS.map(f => f.name)).toContain('targetRef');
    // The zone-token pattern must match where the token legitimately lives —
    // in the store's CODE, not in the prose describing it — or rule 5 would
    // pass by finding nothing anywhere.
    expect(ZONE_TOKEN.test(stripComments(source))).toBe(true);
  });

  it('every WS handler that writes target-scoped state checks the target first', () => {
    const offenders = [];

    for (const handler of WS_HANDLERS) {
      const body = stripComments(handler.body);
      const written = TARGET_SCOPED_REFS.filter(ref =>
        new RegExp(`\\b${ref}\\.value\\s*(?:=|\\.)|Object\\.assign\\(${ref}\\.value`).test(body)
        // filters is mutated through the band objects it holds, not reassigned.
        || (ref === 'filters' && /\bfilter\.(freq|gain|q|type)\s*=/.test(body)),
      );
      if (written.length === 0) continue;

      const checksTarget = SCOPE_CHECKS.some(check => body.includes(check));
      if (!checksTarget) offenders.push(`${handler.name} writes ${written.join(', ')}`);
    }

    expect(offenders).toEqual([]);
  });

  it('only loadStatus reads the per-target record', () => {
    // A second reader is how the master toggle and the sliders came to disagree.
    const readers = FUNCTIONS
      .filter(f => stripComments(f.body).includes('fetchTargetRecord('))
      .map(f => f.name);

    expect(readers).toEqual(['fetchTargetRecord', 'loadStatus']);
  });

  it('spells the zone target in exactly one place', () => {
    // ItemSelector kept its own tab value, `zone:<mac1>,<mac2>`, beside the
    // API's `zone:<zoneId>` — two grammars for "which target is selected",
    // reconciled by three watchers. The mirror could never equal a zone tab's
    // value, so "does the selected tab still exist?" was permanently false and
    // the strip re-selected the zone's first client on every render.
    const offenders = sourceFiles(SRC_DIR)
      .filter(file => file !== EQ_STORE)
      .filter(file => ZONE_TOKEN.test(stripComments(readFileSync(file, 'utf8'))))
      .map(file => relative(SRC_DIR, file));

    expect(offenders).toEqual([]);
  });

  it('no band renders a gain the store has not loaded', () => {
    // cleanup() zeroes the gains so the previous target's curve cannot leak into
    // the next one. But `isConnected` — the gate that mounts the band section —
    // is written only by loadStatus(), so the section re-mounts on the zeroed
    // values and paints a flat curve until the record lands. Measured on the unit
    // (2026-07-27): 3/6 target switches, 6/8 bypass-then-enable, 4/5 re-openings,
    // 59-650 ms each. Flat is indistinguishable from a real `flat` preset, so the
    // fix is to render no gain at all until `filtersLoaded`.
    const cleanup = FUNCTIONS.find(f => f.name === 'cleanup');
    const cleanupBody = stripComments(cleanup.body);
    expect(cleanupBody).toMatch(/filtersLoaded\.value\s*=\s*false/);
    expect(cleanupBody).toMatch(/\.gain\s*=\s*0/);

    const parametric = stripComments(readFileSync(PARAMETRIC_EQ, 'utf8'));
    const bandTag = parametric.match(/<EQBand[\s\S]*?\/>/);
    expect(bandTag, 'no <EQBand> found in ParametricEQ — the extractor is broken').not.toBeNull();
    expect(bandTag[0]).toMatch(/:loaded="filtersLoaded"/);

    const band = stripComments(readFileSync(EQ_BAND, 'utf8'));
    // The printed figure, and the slider whose thumb position is a figure too.
    // The printed figure is the `{{ }}` interpolation alone: a `:class` on the
    // same element mentioning `loaded` must not satisfy this (a surviving mutation
    // proved it would).
    const printed = band.match(/<div class="gain-value[\s\S]*?>\s*(\{\{[\s\S]*?\}\})/);
    const gainSlider = band.match(/<div class="gain-slider"[^>]*>/);
    expect(printed, 'no interpolation inside .gain-value — the extractor is broken').not.toBeNull();
    expect(gainSlider, 'no .gain-slider element in EQBand — the extractor is broken').not.toBeNull();
    expect(printed[1]).toMatch(/\bloaded\b/);
    expect(gainSlider[0]).toMatch(/\bloaded\b/);
  });

  it('the EQ store addresses no volume endpoint', () => {
    // Per-client volume/mute is unifiedAudioStore's — it owns volumeState, and
    // /api/volume/* is not an equalizer surface however the attenuation is done.
    expect(stripComments(source)).not.toMatch(/['"`]\/api\/volume/);
  });
});
