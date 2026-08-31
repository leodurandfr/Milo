// frontend/src/components/gallery/foundations.js
/**
 * The design tokens themselves, as pages of the gallery — the third axis.
 *
 * The catalogue answers "what does this component do", the source pages answer
 * "what does a source look like in every state"; this file answers the question
 * underneath both: what colours, text styles, steps and elevations is any of it
 * allowed to be made of. A reader had to open design-system.css and imagine the
 * result — which is exactly the thing this page exists to stop people doing.
 *
 * ## Parsed, never restated
 *
 * Everything below is read out of `assets/styles/design-system.css` at build
 * time (`?raw`), grouped by the `=== TITLE ===` markers the file already
 * carries. A token added there appears here for free, with no second edit and
 * no chance of a stale copy — the same property the icon grids get from reading
 * their component's own registry.
 *
 * Only the *layout* is declared: which sections a page owns, and how a section
 * is best drawn (a swatch, a bar, a specimen). The guardrail in
 * tests/architecture/gallery.test.js checks that mapping in both directions, so
 * a renamed or new section fails the build rather than silently vanishing from
 * the page.
 *
 * The raw stylesheet is ~20 kB of text in the bundle. It costs an end user
 * nothing: ComponentsView is lazily imported and /components is reachable by URL
 * only.
 *
 * Plain data and no `.vue` import, like catalog.js and sources.js — the
 * guardrail reads this file under Node. `FoundationsPage.vue` draws it.
 */
import css from '@/assets/styles/design-system.css?raw';

/** Prefix that tells a foundation page apart from a catalogue entry in `?c=`. */
export const FOUNDATION_PAGE_PREFIX = 'foundation:';

// Three alternatives, in this order: a section marker, any other comment
// (swallowed whole, so a `--token: value;` written inside prose is not read as a
// declaration), and a declaration.
const DECLARATION_RE = /\/\*\s*===\s*([^=]+?)\s*===\s*\*\/|\/\*[\s\S]*?\*\/|--([\w-]+)\s*:\s*([^;]+);/g;

/**
 * The body of the first `:root {` at or after `from`. Declarations carry no
 * braces and neither does any comment in the file, so the next `}` is the end
 * of the block — the guardrail asserts the parse found the whole thing.
 */
function rootBody(from) {
  const open = css.indexOf('{', css.indexOf(':root', from));
  return css.slice(open + 1, css.indexOf('}', open));
}

/** `:root` body -> [{ title, tokens: [{ name, value }] }], in file order. */
function parseSections(body) {
  const sections = [];
  let current = null;

  DECLARATION_RE.lastIndex = 0;
  let match;
  while ((match = DECLARATION_RE.exec(body)) !== null) {
    const [, heading, name, value] = match;
    if (heading) {
      current = { title: heading, tokens: [] };
      sections.push(current);
    } else if (name && current) {
      current.tokens.push({ name: `--${name}`, value: value.trim().replace(/\s+/g, ' ') });
    }
  }
  return sections;
}

/** Every token section declared on `:root`, in the order the file declares them. */
export const SECTIONS = parseSections(rootBody(0));

/**
 * The `max-aspect-ratio: 4/3` overrides, flattened to name -> value.
 *
 * Shown beside the base value rather than instead of it: this page is read on a
 * desktop browser, which resolves the wide branch, so a reader who is told
 * `--space-06: 32px` and nothing else has no way to learn that it is 24 px on
 * the unit's portrait sibling.
 */
export const MOBILE = Object.fromEntries(
  parseSections(rootBody(css.indexOf('@media (max-aspect-ratio: 4/3)')))
    .flatMap(section => section.tokens)
    .map(token => [token.name, token.value])
);

const BY_NAME = Object.fromEntries(SECTIONS.flatMap(section => section.tokens).map(t => [t.name, t.value]));

/** First `prop: …;` in a rule body. */
function declaration(body, prop) {
  return body.match(new RegExp(`(?:^|[;{\\s])${prop}\\s*:\\s*([^;]+);`))?.[1].trim();
}

/** The token a declaration reads, so a specimen can report its own operands. */
function tokenOf(value) {
  return value?.match(/var\((--[\w-]+)\)/)?.[1];
}

function resolved(value) {
  const token = tokenOf(value);
  return token ? { token, value: BY_NAME[token], mobile: MOBILE[token] } : undefined;
}

/**
 * The typography utility classes, read out of the block between the two markers
 * that bound them. They are what a component actually applies — scoped CSS may
 * not redeclare `font-*` — so the class name is the thing worth showing, and the
 * tokens behind it are read from the class rather than paired up by hand.
 */
export const TYPE_STYLES = (() => {
  const from = css.indexOf('=== UTILITY CLASSES ===');
  const utilities = css.slice(from, css.indexOf('=== GLASSMORPHISM ===', from));
  const styles = [];

  for (const [, className, body] of utilities.matchAll(/\.([\w-]+)\s*\{([^}]*)\}/g)) {
    const family = declaration(body, 'font-family');
    if (!family) continue;
    styles.push({
      className,
      // The fallbacks are the CJK and Devanagari faces every style repeats;
      // the first name is the one that carries the design.
      family: family.match(/'([^']+)'/)?.[1] ?? family,
      weight: declaration(body, 'font-weight'),
      size: resolved(declaration(body, 'font-size')),
      lineHeight: resolved(declaration(body, 'line-height')),
      letterSpacing: resolved(declaration(body, 'letter-spacing'))
    });
  }
  return styles;
})();

/**
 * How a section is best drawn. A colour wants a chip over both backgrounds, a
 * step wants a bar you can compare to the one above it, a radius wants a corner.
 */
const KINDS = {
  'PRIMARY COLORS': 'swatch',
  'TEXT COLORS': 'swatch',
  'BACKGROUND COLORS': 'swatch',
  BORDERS: 'swatch',
  'SYSTEM COLORS': 'swatch',
  'SVG BRAND TOKENS': 'swatch',
  STROKES: 'swatch',
  'SOURCE GRADIENTS': 'swatch',
  SPACING: 'space',
  'BORDER RADIUS': 'radius',
  'TEXT STYLES': 'tokens',
  SHADOWS: 'shadow',
  BLUR: 'blur'
};

/**
 * A `:root` section deliberately absent from every page. The reason is the
 * point, as in catalog.js: it is what the next person has to disagree with in
 * writing before adding one.
 */
export const EXCLUDED_SECTIONS = {
  'EASING PRESETS':
    'A curve cannot be judged from a still image — it has to be replayed, and this page is a printed reference with no interaction at all. Read the springs in the components that apply them.',
  TRANSITIONS:
    'Same as the easings it composes: a duration shown as text is the number already written in design-system.css, and showing it running would make this page a playground.'
};

/** Prose a section deserves beyond its own token list. */
const NOTES = {
  'TEXT COLORS': 'Each chip sits half on --color-background-neutral and half on --color-background-contrast, so the alpha variants read as what they are.',
  'BACKGROUND COLORS': 'The four contrast tones and the two neutral-alpha ones are the same colour at four opacities — the split backing is what tells them apart.',
  STROKES: 'A whole gradient rather than a colour, because it belongs to no ramp: the glass stroke is one value, applied by .glass-border.',
  'SOURCE GRADIENTS': 'The tint AudioSourceLayout washes behind a browsing source. Three one-off brand colours, which is why they are gradients here and not tokens in a ramp.',
  SPACING: 'A step that shrinks below 4:3 shows its portrait value beside the base one — and --space-05-fixed is the one that deliberately does not.',
  'TEXT STYLES': 'The raw operands. What a component applies is the utility class below, never these directly.',
  BLUR: 'Drawn as backdrop-filter over a fixed backdrop, which is how every one of them is used.'
};

function sectionBlock(title) {
  const section = SECTIONS.find(entry => entry.title === title);
  if (!section) return null;

  return {
    title,
    kind: KINDS[title],
    note: NOTES[title],
    tokens: section.tokens.map(token => ({ ...token, mobile: MOBILE[token.name] }))
  };
}

/**
 * The four pages, each naming the `:root` sections it owns. Splitting colours
 * from type from measurement from elevation keeps a page one screen tall, which
 * is the same fix the sidebar was for: a single "Foundations" page would be the
 * four-screen scroll this gallery was built to replace.
 */
const PAGES = [
  {
    id: 'colors',
    title: 'Colours',
    summary: 'Every colour the app is allowed to be: the brand tone, the text and background ramps with the alpha variants of each, the system colours, and the gradients that belong to no ramp at all.',
    sections: ['PRIMARY COLORS', 'TEXT COLORS', 'BACKGROUND COLORS', 'BORDERS', 'SYSTEM COLORS', 'SVG BRAND TOKENS', 'STROKES', 'SOURCE GRADIENTS'],
    extras: []
  },
  {
    id: 'typography',
    title: 'Typography',
    summary: 'Two families and the utility classes that apply them. Typography is applied by class — scoped CSS may not redeclare font-size, line-height or letter-spacing — so the class names below are the whole vocabulary a component has.',
    sections: ['TEXT STYLES'],
    extras: [
      {
        title: 'UTILITY CLASSES',
        kind: 'specimen',
        note: 'Rendered at the size this browser resolves. The mobile column is what the same class draws below 4:3.',
        styles: TYPE_STYLES
      }
    ]
  },
  {
    id: 'spacing',
    title: 'Spacing & radius',
    summary: 'The two measurement scales: the spacing steps, five of which shrink on a portrait viewport, and the corner radii, ending at a pill. Every gap and every corner in the app is one of these.',
    sections: ['SPACING', 'BORDER RADIUS'],
    extras: []
  },
  {
    id: 'elevation',
    title: 'Elevation & blur',
    summary: 'What lifts a surface off the one below it: the shadow casts across their four intents (ambient, raised, the artwork halo, hairline), the blur radii, and the two glass utilities that combine a blur with a stroke.',
    sections: ['SHADOWS', 'BLUR'],
    extras: [
      {
        title: 'GLASSMORPHISM',
        kind: 'glass',
        note: 'Applied as classes, tuned through four local custom properties (--glass-bg, --glass-blur, --glass-radius, --glass-stroke-width). Both are drawn here over the same backdrop as the blur steps.',
        variants: [
          { label: '.glass-surface', classes: 'glass-surface' },
          { label: '.glass-surface .glass-border', classes: 'glass-surface glass-border' }
        ]
      }
    ]
  }
];

export const FOUNDATION_PAGES = PAGES.map(page => ({
  id: `${FOUNDATION_PAGE_PREFIX}${page.id}`,
  title: page.title,
  summary: page.summary,
  sections: page.sections,
  blocks: [...page.sections.map(sectionBlock).filter(Boolean), ...page.extras]
}));

export function isFoundationId(id) {
  return typeof id === 'string' && id.startsWith(FOUNDATION_PAGE_PREFIX);
}

export function foundationPageById(id) {
  return FOUNDATION_PAGES.find(page => page.id === id);
}
