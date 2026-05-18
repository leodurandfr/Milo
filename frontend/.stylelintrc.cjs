/**
 * Stylelint floor for the Milō frontend (RFC 21 PR 3).
 *
 * Hard rules (must NOT be silenced ad-hoc):
 *   1. No hex literals in scoped CSS — use a design token.
 *   2. No `rgba(...)` / `rgb(...)` / `hsla(...)` / `hsl(...)` color literals
 *      in scoped CSS — use a token (extend design-system.css if a shade is
 *      missing).
 *   3. No typography redefinitions in scoped CSS (font-family / font-size /
 *      line-height / letter-spacing / font-weight) — apply a utility class
 *      (heading-1..4, text-body, text-mono, display-1) on the element
 *      instead.
 *
 * Soft rules from stylelint-config-standard that produce noise on the
 * current codebase are turned off (no-descending-specificity,
 * selector-class-pattern, etc.) — the goal of this floor is to lock the
 * three hard rules above, not to enforce arbitrary style conventions.
 *
 * Overrides whitelist files where a hex / rgba is sémantiquement justifié:
 *   - design-system.css        — the token catalogue itself
 *   - StyleGuide / UIComponentsGuide — dev-only views (not shipped in
 *     normal use, kept for visual reference)
 *   - AudioScreensaver         — pure-black overlays for the screensaver
 *   - AudioSourceLayout        — ad-hoc brand gradients (one-off visuals)
 *   - ManageStation / PodcastSettings — intermediate error-shade borders
 *     (canonical token would lose semantic fidelity, see RFC 21 plan).
 *
 * When stylelint rejects something:
 *   (a) Token is missing → extend frontend/src/assets/styles/design-system.css.
 *   (b) Truly one-off (gradient, SVG inline pure-black) → add the file to
 *       the overrides list below with a one-line comment explaining why.
 *   (c) Never add `// stylelint-disable` comments inline — see CLAUDE.md
 *       Common Pitfalls #19.
 */
module.exports = {
  extends: [
    'stylelint-config-standard',
    'stylelint-config-recommended-vue',
  ],
  rules: {
    'color-no-hex': [true, {
      message: 'Use a design token from frontend/src/assets/styles/design-system.css instead of a hex literal',
    }],
    'declaration-property-value-disallowed-list': {
      '/.+/': ['/rgba?\\(/', '/hsla?\\(/'],
      'font-family': ['/.+/'],
      'font-size': ['/.+/'],
      'line-height': ['/.+/'],
      'letter-spacing': ['/.+/'],
      'font-weight': ['/.+/'],
    },
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
      files: [
        'src/assets/styles/design-system.css',
        'src/assets/styles/app.css',
        'src/views/StyleGuide.vue',
        'src/views/UIComponentsGuide.vue',
        'src/views/CardsStyleGuide.vue',
        // Pure-black overlays + brand gradients (one-off visuals).
        'src/components/audio/AudioScreensaver.vue',
        'src/components/audio/AudioSourceLayout.vue',
        // Intermediate error-shade borders that lose semantic fidelity
        // if mapped to --color-error-subtle (kept local per RFC 21 plan).
        'src/components/settings/categories/radio/ManageStation.vue',
        'src/components/settings/categories/PodcastSettings.vue',
        // linear-gradient(#000 0 0) CSS-mask composition (not a color
        // semantic) + skeleton ::before placeholders that must mimic
        // exact heading-3 dimensions (no class possible on pseudo-element).
        'src/components/ui/Modal.vue',
        'src/components/ui/VirtualKeyboard.vue',
        'src/components/settings/categories/multiroom/ClientEdit.vue',
        // AudioPlayer: linear-gradient(#000 0 0) mask CSS.
        // AudioPlayerFull: ultra-subtle shadow (~5% black, no token).
        'src/components/audio/AudioPlayer.vue',
        'src/components/audio/AudioPlayerFull.vue',
        // EqualizerModal: error banner uses sizes outside the type scale
        // (13/16/18px) for compact inline icon + dismiss glyph.
        // LevelMeter: scale markers at 9px and 11px — smaller than the
        // smallest design-system token (--font-size-mono-small=14px).
        'src/components/equalizer/EqualizerModal.vue',
        'src/components/equalizer/LevelMeter.vue',
      ],
      rules: {
        'color-no-hex': null,
        'declaration-property-value-disallowed-list': null,
      },
    },
  ],
};
