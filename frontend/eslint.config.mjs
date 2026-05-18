// frontend/eslint.config.js
// Flat config (ESLint v9). Rules are activated progressively per lot —
// see docs/plans/exec-lint-floor.md. Bootstrap (B0): no rules active beyond
// the eslint-plugin-vue recommended baseline. Lot A adds axios + console
// restrictions.
import pluginVue from 'eslint-plugin-vue';

export default [
  ...pluginVue.configs['flat/recommended'],
  {
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        window: 'readonly',
        document: 'readonly',
        navigator: 'readonly',
        console: 'readonly',
        process: 'readonly',
        setTimeout: 'readonly',
        clearTimeout: 'readonly',
        setInterval: 'readonly',
        clearInterval: 'readonly',
        requestAnimationFrame: 'readonly',
        cancelAnimationFrame: 'readonly',
        URL: 'readonly',
        URLSearchParams: 'readonly',
        WebSocket: 'readonly',
        AbortController: 'readonly',
        FormData: 'readonly',
        Blob: 'readonly',
        File: 'readonly',
        FileReader: 'readonly',
        Image: 'readonly',
        fetch: 'readonly',
        localStorage: 'readonly',
        sessionStorage: 'readonly',
        location: 'readonly',
        history: 'readonly',
        alert: 'readonly',
        confirm: 'readonly',
        Event: 'readonly',
        CustomEvent: 'readonly',
        MutationObserver: 'readonly',
        IntersectionObserver: 'readonly',
        ResizeObserver: 'readonly',
        HTMLElement: 'readonly',
        Element: 'readonly',
        Node: 'readonly',
        getComputedStyle: 'readonly',
      },
    },
    rules: {
      // === RFC 22 Lot A — RFC 17 lock ===
      // Ban direct axios usage. Every HTTP request must flow through
      // frontend/src/services/apiCall.js (typed helpers, central logging,
      // resilience-pattern + AbortController plumbing). Whitelisted below
      // for apiCall.js itself.
      'no-restricted-imports': ['error', {
        paths: [{
          name: 'axios',
          message: "Use api helpers from '@/services/apiCall' instead. See CLAUDE.md \"Frontend Conventions\" + RFC 17.",
        }],
      }],
      // Ban console.* — use logger.{debug,info,warn,error}(category, message, data)
      // from '@/services/logger' so messages get the category prefix and central
      // routing. Whitelisted below for logger.js, main.js, schemas/api.js, modalDebug.js.
      'no-restricted-syntax': ['error', {
        selector: "CallExpression[callee.object.name='console'][callee.property.name=/^(error|log|debug|warn|info)$/]",
        message: "Use logger.{debug,info,warn,error}() from '@/services/logger' instead. See CLAUDE.md \"Frontend Conventions\" + RFC 17.",
      }],

      // Disable the noisier eslint-plugin-vue defaults. The point of this
      // config is to lock the rules of RFCs 17-21, not to enforce arbitrary
      // style conventions on top of the current codebase.
      'vue/multi-word-component-names': 'off',
      'vue/no-v-html': 'off',
      'vue/attribute-hyphenation': 'off',
      'vue/v-on-event-hyphenation': 'off',
      'vue/no-template-shadow': 'off',
      'vue/require-default-prop': 'off',
      'vue/require-explicit-emits': 'off',
      'vue/no-mutating-props': 'off',
      'vue/component-definition-name-casing': 'off',
      'vue/prop-name-casing': 'off',
      'vue/attributes-order': 'off',
      'vue/order-in-components': 'off',
      'vue/html-self-closing': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/max-attributes-per-line': 'off',
      'vue/html-closing-bracket-newline': 'off',
      'vue/html-indent': 'off',
      'vue/first-attribute-linebreak': 'off',
      'vue/no-multi-spaces': 'off',
      'vue/html-quotes': 'off',
      'vue/mustache-interpolation-spacing': 'off',
      'vue/no-spaces-around-equal-signs-in-attribute': 'off',
      'vue/component-tags-order': 'off',
      'vue/this-in-template': 'off',
      'vue/no-parsing-error': 'off',
      'vue/no-reserved-component-names': 'off',
      'vue/return-in-computed-property': 'off',
      'vue/no-unused-components': 'off',
      'vue/no-unused-vars': 'off',
      'vue/no-side-effects-in-computed-properties': 'off',
      'vue/no-dupe-keys': 'off',
      'vue/require-v-for-key': 'off',
      'vue/valid-v-for': 'off',
      'vue/no-deprecated-slot-attribute': 'off',
      'vue/no-deprecated-v-on-native-modifier': 'off',
      'vue/no-deprecated-slot-scope-attribute': 'off',
      'vue/multiline-html-element-content-newline': 'off',
      'vue/no-v-text-v-html-on-component': 'off',
      'vue/require-prop-types': 'off',
      'vue/require-valid-default-prop': 'off',
    },
  },
  // === RFC 22 Lot A whitelists ===
  // apiCall.js is the one site allowed to import axios — it IS the helper layer.
  {
    files: ['src/services/apiCall.js'],
    rules: {
      'no-restricted-imports': 'off',
    },
  },
  // logger.js is the only site allowed to call console.* — it IS the logger.
  // main.js Vue errorHandler, schemas/api.js dev-only Zod warnings, and
  // modalDebug.js opt-in debug toggle are the other documented exceptions
  // (see CLAUDE.md "Frontend Conventions").
  {
    files: [
      'src/services/logger.js',
      'src/main.js',
      'src/schemas/api.js',
      'src/services/modalDebug.js',
    ],
    rules: {
      'no-restricted-syntax': 'off',
    },
  },
  {
    ignores: [
      'dist/**',
      'node_modules/**',
      'public/**',
      'scripts/**',
      '*.config.js',
      'vite.config.js',
    ],
  },
];
