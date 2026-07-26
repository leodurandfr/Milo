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
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_DIR = resolve(HERE, '../../src');
const EQ_STORE = join(SRC_DIR, 'stores/equalizerStore.js');

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

  it('the EQ store addresses no volume endpoint', () => {
    // Per-client volume/mute is unifiedAudioStore's — it owns volumeState, and
    // /api/volume/* is not an equalizer surface however the attenuation is done.
    expect(stripComments(source)).not.toMatch(/['"`]\/api\/volume/);
  });
});
