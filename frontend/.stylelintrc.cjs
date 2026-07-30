/**
 * Stylelint floor for the Milō frontend.
 *
 * Three hard rules, none of which may be silenced ad-hoc:
 *   1. No hex literals in scoped CSS — use a design token.        (color-no-hex)
 *   2. No `rgba()/rgb()/hsla()/hsl()` colour literals in scoped
 *      CSS — use a token (extend design-system.css if a shade is
 *      missing).                                                  (NO_COLOR_FN)
 *   3. No typography redefinition in scoped CSS (font-family /
 *      font-size / line-height / letter-spacing / font-weight) —
 *      apply a utility class (heading-1..4, text-body, text-mono,
 *      display-1) on the element instead.                         (NO_TYPOGRAPHY)
 *
 * Rules 2 and 3 share one stylelint rule
 * (declaration-property-value-disallowed-list), so they are declared as two
 * halves below and recombined. That split is the point: an exemption names
 * *which* of the three rules it buys, instead of nulling all three. A file
 * exempted for a `linear-gradient(#000 0 0)` mask should not thereby be free to
 * hardcode a font-size.
 *
 * Soft rules from stylelint-config-standard that produce noise on the current
 * codebase are turned off — the goal of this floor is to lock the three hard
 * rules, not to enforce arbitrary style conventions.
 *
 * When stylelint rejects something:
 *   (a) A token is missing → extend frontend/src/assets/styles/design-system.css.
 *       "Missing" includes a value that already appears in more than one file:
 *       a repeated literal is a token that was never declared, not a one-off.
 *   (b) Truly one-off (a mask gradient, a brand gradient, a glyph smaller than
 *       the type scale) → add the file below, under the rule it actually needs,
 *       with a one-line reason.
 *   (c) Never add an inline `// stylelint-disable` — see CLAUDE.md § Frontend
 *       conventions.
 */

const NO_COLOR_FN = {
  '/.+/': ['/rgba?\\(/', '/hsla?\\(/'],
};

const NO_TYPOGRAPHY = {
  'font-family': ['/.+/'],
  'font-size': ['/.+/'],
  'line-height': ['/.+/'],
  'letter-spacing': ['/.+/'],
  'font-weight': ['/.+/'],
};

const HEX_MESSAGE =
  'Use a design token from frontend/src/assets/styles/design-system.css instead of a hex literal';

module.exports = {
  extends: [
    'stylelint-config-standard',
    'stylelint-config-recommended-vue',
  ],
  rules: {
    'color-no-hex': [true, { message: HEX_MESSAGE }],
    'declaration-property-value-disallowed-list': { ...NO_COLOR_FN, ...NO_TYPOGRAPHY },
    // Soft rules from stylelint-config-standard turned off — too noisy on
    // an existing codebase and not in scope for this lint floor.
    'no-descending-specificity': null,
    'selector-class-pattern': null,
    'custom-property-pattern': null,
    'keyframes-name-pattern': null,
    'value-keyword-case': null,
    'function-name-case': null,
    'declaration-block-no-redundant-longhand-properties': null,
    'shorthand-property-no-redundant-values': null,
    'declaration-block-no-shorthand-property-overrides': null,
    'no-duplicate-selectors': null,
    'media-feature-range-notation': null,
    'alpha-value-notation': null,
    'color-function-notation': null,
    'hue-degree-notation': null,
    'length-zero-no-unit': null,
    'comment-empty-line-before': null,
    'rule-empty-line-before': null,
    'declaration-empty-line-before': null,
    'custom-property-empty-line-before': null,
    'at-rule-empty-line-before': null,
    'at-rule-no-vendor-prefix': null,
    'property-no-vendor-prefix': null,
    'value-no-vendor-prefix': null,
    'selector-no-vendor-prefix': null,
    'media-feature-name-no-vendor-prefix': null,
    'number-max-precision': null,
    'declaration-block-single-line-max-declarations': null,
    'no-empty-source': null,
    'no-invalid-position-at-import-rule': null,
    'import-notation': null,
    'media-query-no-invalid': null,
    'comment-whitespace-inside': null,
    'color-function-alias-notation': null,
    'selector-pseudo-element-colon-notation': null,
    'declaration-property-value-keyword-no-deprecated': null,
    'color-hex-length': null,
    'property-no-deprecated': null,
    'font-family-no-missing-generic-family-keyword': null,
  },
  overrides: [
    {
      // The token catalogue itself — it is where the literals are supposed to be.
      files: ['src/assets/styles/design-system.css'],
      rules: {
        'color-no-hex': null,
        'declaration-property-value-disallowed-list': null,
      },
    },
    {
      // `linear-gradient(#000 0 0)` CSS-mask composition — the hex is a mask
      // channel, not a colour semantic.
      files: [
        'src/components/audio/AudioPlayer.vue',
        'src/components/ui/Modal.vue',
      ],
      rules: {
        'color-no-hex': null,
      },
    },
    {
      // Screensaver: pure-black backdrop + its dim scrim.
      files: ['src/components/audio/AudioScreensaver.vue'],
      rules: {
        'color-no-hex': null,
        'declaration-property-value-disallowed-list': NO_TYPOGRAPHY,
      },
    },
    {
      // An intermediate error-shade border: --color-error-subtle (12%) is too
      // faint for a 2px stroke and --color-error too loud.
      files: ['src/components/settings/categories/radio/ManageStation.vue'],
      rules: {
        'declaration-property-value-disallowed-list': NO_TYPOGRAPHY,
      },
    },
    {
      // ClientEdit: a skeleton `::before` reserving exact heading-3 metrics —
      // a utility class cannot be applied to a pseudo-element.
      // LevelMeter: scale markers at 9px and 11px, below the smallest type
      // token (--font-size-mono-small = 14px, 12px on mobile).
      files: [
        'src/components/settings/categories/multiroom/ClientEdit.vue',
        'src/components/equalizer/LevelMeter.vue',
      ],
      rules: {
        'declaration-property-value-disallowed-list': NO_COLOR_FN,
      },
    },
    {
      // Mask composition (as above) + font-size on the display <input> and the
      // accent options, which are sized from the same geometry as the keys.
      files: ['src/components/ui/VirtualKeyboard.vue'],
      rules: {
        'color-no-hex': null,
        'declaration-property-value-disallowed-list': NO_COLOR_FN,
      },
    },
  ],
};
