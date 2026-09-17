// frontend/tests/architecture/iconScale.test.js
/**
 * Structural guardrail over the transport icon scale.
 *
 * Playback control sizes used to be `:deep()` overrides carrying raw pixels,
 * recopied in three files — AudioPlayer twice and LyricsPlaybackBar once, whose
 * own comment said "copied verbatim". Nothing could see them diverge, and one of
 * the four transport rows had quietly lost its hierarchy entirely: prev, play
 * and next all drawn at the same size. These three checks are what makes that
 * class of drift fail the build instead of waiting to be noticed by eye.
 *
 * Mounts nothing, asserts no markup: it reads the sources the browser reads.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, relative } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, '../../src');
const DESIGN_SYSTEM = join(SRC, 'assets/styles/design-system.css');

/** The two files allowed to state an icon dimension in pixels. */
const SIZE_OWNERS = ['assets/styles/design-system.css', 'components/ui/SvgIcon.vue'];

/**
 * Files allowed to put a pixel value on the two variables that drive icon size.
 * LoadingSpinner declares its own rungs the same way SvgIcon does, and Button
 * owns a spinner scale of its own that has nothing to do with the transport.
 */
const VAR_OWNERS = [
  ...SIZE_OWNERS,
  'components/ui/LoadingSpinner.vue',
  'components/ui/Button.vue'
];

const ROLES = ['primary', 'secondary', 'secondary-round', 'utility'];

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    return statSync(full).isDirectory() ? walk(full) : [full];
  });
}

const STYLE_FILES = walk(SRC).filter((f) => f.endsWith('.vue') || f.endsWith('.css'));
const rel = (f) => relative(SRC, f);

describe('transport icon scale', () => {
  it('leaves every icon dimension to the design system', () => {
    // A rule that targets .svg-responsive and hardcodes a pixel dimension is the
    // exact shape that was recopied across three files. Anywhere but the two
    // owners, it is a size the scale cannot reach and no tier can override.
    const offenders = [];

    for (const file of STYLE_FILES) {
      if (SIZE_OWNERS.includes(rel(file))) continue;
      const text = readFileSync(file, 'utf8');
      for (const rule of text.matchAll(/([^{}]*\.svg-responsive[^{}]*)\{([^}]*)\}/g)) {
        if (/\d+px/.test(rule[2])) {
          offenders.push(`${rel(file)}: ${rule[1].trim().replace(/\s+/g, ' ')}`);
        }
      }
    }

    // Same for the variables themselves. The overrides this scale replaced were
    // `--spinner-size: 44px` recopied in three files, and they targeted no
    // .svg-responsive selector at all — the rule above would let every one of
    // them come back green.
    for (const file of STYLE_FILES) {
      if (VAR_OWNERS.includes(rel(file))) continue;
      const text = readFileSync(file, 'utf8');
      for (const decl of text.matchAll(/--(svg-size|spinner-size)\s*:\s*([^;}]+)/g)) {
        if (/\d+px/.test(decl[2])) {
          offenders.push(`${rel(file)}: --${decl[1]}: ${decl[2].trim()}`);
        }
      }
    }

    // Both extractors must prove they can see the shapes they forbid, or a
    // broken regex would report "no offenders" forever.
    const owner = readFileSync(join(SRC, 'components/ui/SvgIcon.vue'), 'utf8');
    const seen = [...owner.matchAll(/([^{}]*\.svg-responsive[^{}]*)\{([^}]*)\}/g)];
    expect(seen.length).toBeGreaterThan(3);
    expect(seen.some(([, , body]) => /\d+px/.test(body))).toBe(true);

    const button = readFileSync(join(SRC, 'components/ui/Button.vue'), 'utf8');
    expect([...button.matchAll(/--spinner-size\s*:\s*\d+px/g)].length).toBeGreaterThan(0);

    expect(offenders).toEqual([]);
  });

  it('keeps the tiers on one proportion', () => {
    // The sizes themselves are a judgement call and move; what must not move is
    // that a smaller tier is the *same shape* as the one above it, just smaller.
    // The 4px grid rounds the two ratios a few percent apart and cannot do
    // better, so the check is a tolerance, not equality — wide enough to survive
    // rounding, tight enough that a tier retuned on its own fails. Everything
    // asserted here is a relation between declared tokens, never a value this
    // test wrote.
    const TIER_TOLERANCE = 0.06;
    const css = readFileSync(DESIGN_SYSTEM, 'utf8');

    const tierOf = (selector) => {
      const body = css.match(
        new RegExp(`${selector.replace(/[.\\-]/g, '\\$&')}\\s*\\{([^}]*)\\}`)
      );
      expect(body, `${selector} is not declared`).not.toBeNull();
      return Object.fromEntries(
        ROLES.map((role) => {
          const found = body[1].match(new RegExp(`--transport-${role}:\\s*(\\d+)px`));
          expect(found, `${selector} declares no --transport-${role}`).not.toBeNull();
          return [role, Number(found[1])];
        })
      );
    };

    // Ordered widest first; the loop below reads that order as the hierarchy.
    const tiers = {
      '.transport-scale': tierOf('.transport-scale'),
      '.transport-scale--phone': tierOf('.transport-scale--phone'),
      '.transport-scale--compact': tierOf('.transport-scale--compact')
    };

    for (const [name, tier] of Object.entries(tiers)) {
      const { primary, secondary, utility } = tier;
      const round = tier['secondary-round'];
      expect(ROLES.map((r) => tier[r]).every((v) => v % 4 === 0), `${name} off the 4px grid`).toBe(true);
      expect(primary, `${name}: primary must lead`).toBeGreaterThan(secondary);
      expect(secondary, `${name}: secondary must lead utility`).toBeGreaterThan(utility);
      expect(primary / utility, `${name}: utility is not subordinate enough`).toBeGreaterThanOrEqual(2);
      // secondary-round is the same flanking role for a glyph that fills its
      // box, so it sits strictly between the two: level with `secondary` it
      // would be the oversized pair it exists to fix, level with `utility` its
      // digits stop being readable.
      expect(round, `${name}: secondary-round must sit under secondary`).toBeLessThan(secondary);
      expect(round, `${name}: secondary-round must sit above utility`).toBeGreaterThan(utility);
    }

    // `secondary-round` answers to a ceiling, not to a proportion: a glyph that
    // fills its box may not outgrow the `pause` it flanks, or the flanking
    // control reads as the main one — which is the inversion this role was
    // added to end. The ring's extent is read from the clipPath its own file
    // declares, so a redrawn glyph moves the ceiling with it; the pause's 16.0
    // units are stated, its path being too involved to parse for one number.
    const ringHeight = (() => {
      const svg = readFileSync(join(SRC, 'assets/icons/rewind-15.svg'), 'utf8');
      const clip = svg.match(/<clipPath[^>]*>\s*<path[^>]*\sd="([^"]+)"/);
      expect(clip, 'rewind-15 no longer declares the clipPath its extent is read from').not.toBeNull();
      // `M2.625.938h18.677V21.39H2.625z` — SVG drops the separator between a
      // number and a following decimal, so split on the number grammar rather
      // than on whitespace: [x0, y0, width, yBottom, ...].
      const n = clip[1].match(/\d+\.\d+|\.\d+|\d+/g).map(Number);
      return n[3] - n[1];
    })();
    // A parse that silently yields nothing must fail here, not pass on an empty
    // surface: every other glyph in the set stands between 12 and 21 units.
    expect(ringHeight, 'the ring extent parsed to something implausible').toBeGreaterThan(19);
    expect(ringHeight, 'the ring extent parsed to something implausible').toBeLessThan(22);
    const PAUSE_HEIGHT = 16.0;

    for (const [name, tier] of Object.entries(tiers)) {
      const ceiling = (tier.primary * PAUSE_HEIGHT) / ringHeight;
      expect(
        tier['secondary-round'],
        `${name}: secondary-round outgrows the pause it flanks (ceiling ${ceiling.toFixed(1)}px)`
      ).toBeLessThanOrEqual(ceiling);
    }

    // Both proportions are checked, not just the first: a tier retuned on its
    // own is drift whichever pair of roles it breaks. The round pair gets a
    // wider band because the ceiling above is what sets it and the 4px grid
    // then rounds it down — one grid step is already 12% on values near 32.
    for (const [lead, follow, tolerance] of [
      ['primary', 'secondary', TIER_TOLERANCE],
      ['secondary', 'secondary-round', 0.1]
    ]) {
      const ratios = Object.values(tiers).map((t) => t[lead] / t[follow]);
      expect(
        (Math.max(...ratios) - Math.min(...ratios)) / Math.min(...ratios),
        `the tiers no longer read as one proportion on ${lead}/${follow}`
      ).toBeLessThan(tolerance);
    }

    // Each tier is strictly smaller than the one above it, or it has no reason
    // to exist as a separate tier.
    const primaries = Object.values(tiers).map((t) => t.primary);
    expect(
      primaries.every((p, i) => i === 0 || p < primaries[i - 1]),
      `tiers are not strictly decreasing: ${primaries.join(' > ')}`
    ).toBe(true);
  });

  it('matches every declared class against the rows and buttons that wear it', () => {
    // Three ways this goes wrong, all silent in a browser: a button wearing a
    // class the design system never declared (drawn at IconButton's native size
    // while its neighbours follow the scale), a declaration nobody wears (a tier
    // that stopped being applied), and the role trio itself losing a member.
    const css = readFileSync(DESIGN_SYSTEM, 'utf8');
    const declared = new Set(
      [...css.matchAll(/\.(transport-[a-z-]+)\s*\{/g)].map((m) => m[1])
    );

    const worn = new Map();
    for (const file of STYLE_FILES.filter((f) => f.endsWith('.vue'))) {
      const text = readFileSync(file, 'utf8');
      for (const attr of text.matchAll(/:?class="([^"]*)"/g)) {
        // Whole class tokens only: a bare /transport-/ also matches inside
        // `ml-transport-main`, which is a layout hook and not a scale rung.
        for (const cls of attr[1].matchAll(/(?:^|[\s'"])(transport-[a-z-]+)(?=$|[\s'"])/g)) {
          worn.set(cls[1], (worn.get(cls[1]) ?? 0) + 1);
        }
      }
    }

    expect([...worn.keys()].sort(), 'a class no tier declares').toEqual([...declared].sort());
    expect(
      ROLES.map((role) => `transport-${role}`).filter((cls) => !declared.has(cls)),
      'the role trio lost a member'
    ).toEqual([]);

    // Every transport row in the app wears a primary; a count of one would mean
    // the scale reached a single component.
    expect(worn.get('transport-primary')).toBeGreaterThan(3);
  });
});
