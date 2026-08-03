<!-- frontend/src/components/gallery/FoundationsPage.vue -->
<!--
  Draws one foundations page: the token blocks foundations.js parsed out of
  design-system.css, each rendered the way its kind is best read.

  Nothing here is interactive and nothing is a control. A token is not a
  component — there is no prop to drive and no state to fake — so this pane
  replaces the canvas iframe rather than living inside it, and the props panel
  stays away. The one thing worth showing that plain text cannot is the token
  *applied*: a chip painted with the colour, a bar as wide as the step, a corner
  actually cut, a blur over something busy enough to blur.
-->
<template>
  <article class="foundations">
    <section v-for="block in page.blocks" :key="block.title" class="foundations__block">
      <header class="foundations__head">
        <h3 class="foundations__title text-mono-small">{{ block.title }}</h3>
        <p v-if="block.note" class="foundations__note text-mono-small">{{ block.note }}</p>
      </header>

      <!-- Colours and gradients: the chip straddles both backgrounds, which is
           the only way an alpha variant reads as anything but a flat tone. -->
      <ul v-if="block.kind === 'swatch'" class="foundations__grid foundations__grid--swatch">
        <li v-for="token in block.tokens" :key="token.name" class="foundations__cell">
          <span class="swatch">
            <span class="swatch__fill" :style="{ background: `var(${token.name})` }" />
          </span>
          <p class="foundations__name text-mono-small">{{ token.name }}</p>
          <p class="foundations__value text-mono-small" :title="token.value">{{ token.value }}</p>
        </li>
      </ul>

      <ul v-else-if="block.kind === 'space'" class="foundations__rows">
        <li v-for="token in block.tokens" :key="token.name" class="foundations__row">
          <span class="foundations__bar" :style="{ width: `var(${token.name})` }" />
          <span class="foundations__name text-mono-small">{{ token.name }}</span>
          <span class="foundations__value text-mono-small">
            {{ token.value }}<em v-if="token.mobile" class="foundations__mobile"> · 4:3 {{ token.mobile }}</em>
          </span>
        </li>
      </ul>

      <ul v-else-if="block.kind === 'radius'" class="foundations__grid foundations__grid--tile">
        <li v-for="token in block.tokens" :key="token.name" class="foundations__cell">
          <span class="corner" :style="{ borderRadius: `var(${token.name})` }" />
          <p class="foundations__name text-mono-small">{{ token.name }}</p>
          <p class="foundations__value text-mono-small">{{ token.value }}</p>
        </li>
      </ul>

      <ul v-else-if="block.kind === 'shadow'" class="foundations__grid foundations__grid--tile">
        <li v-for="token in block.tokens" :key="token.name" class="foundations__cell">
          <span class="cast" :style="{ boxShadow: `var(${token.name})` }" />
          <p class="foundations__name text-mono-small">{{ token.name }}</p>
          <p class="foundations__value text-mono-small" :title="token.value">{{ token.value }}</p>
        </li>
      </ul>

      <ul v-else-if="block.kind === 'blur'" class="foundations__grid foundations__grid--tile backdrop">
        <li v-for="token in block.tokens" :key="token.name" class="foundations__cell">
          <span class="pane" :style="{ backdropFilter: `blur(var(${token.name}))` }" />
          <p class="foundations__name foundations__name--over text-mono-small">{{ token.name }}</p>
          <p class="foundations__value foundations__value--over text-mono-small">{{ token.value }}</p>
        </li>
      </ul>

      <ul v-else-if="block.kind === 'glass'" class="foundations__grid foundations__grid--tile backdrop">
        <li v-for="variant in block.variants" :key="variant.label" class="foundations__cell">
          <span class="glass-tile" :class="variant.classes" />
          <p class="foundations__name foundations__name--over text-mono-small">{{ variant.label }}</p>
        </li>
      </ul>

      <!-- The eight utility classes, each drawn with itself. -->
      <ul v-else-if="block.kind === 'specimen'" class="foundations__rows foundations__rows--specimen">
        <li v-for="style in block.styles" :key="style.className" class="foundations__specimen">
          <p :class="style.className" class="foundations__sample">{{ SAMPLE }}</p>
          <p class="foundations__name text-mono-small">.{{ style.className }}</p>
          <p class="foundations__value text-mono-small">
            {{ style.family }} {{ style.weight }} ·
            {{ style.size?.value }}/{{ style.lineHeight?.value }} ·
            {{ style.letterSpacing?.value }}
            <em v-if="style.size?.mobile" class="foundations__mobile">
              · 4:3 {{ style.size.mobile }}/{{ style.lineHeight?.mobile }}
            </em>
          </p>
        </li>
      </ul>

      <!-- Raw operands: nothing to draw that the specimens do not draw better.
           Spelled out rather than left as a `v-else`, so that the guardrail can
           read every kind this file can draw off these comparisons — a block
           whose kind has no branch here is caught in the suite instead of
           quietly rendering as a list of numbers. -->
      <ul v-else-if="block.kind === 'tokens'" class="foundations__rows">
        <li v-for="token in block.tokens" :key="token.name" class="foundations__row foundations__row--plain">
          <span class="foundations__name text-mono-small">{{ token.name }}</span>
          <span class="foundations__value text-mono-small">
            {{ token.value }}<em v-if="token.mobile" class="foundations__mobile"> · 4:3 {{ token.mobile }}</em>
          </span>
        </li>
      </ul>
    </section>
  </article>
</template>

<script setup>
/** One line carrying both cases, the digits and a diacritic — the ō included. */
const SAMPLE = 'Milō sounds better — 0123';

defineProps({
  /** A FOUNDATION_PAGES entry: title, summary, and its parsed blocks. */
  page: {
    type: Object,
    required: true
  }
});
</script>

<style scoped>
.foundations {
  display: flex;
  flex-direction: column;
  gap: var(--space-06);
  padding-bottom: var(--space-06);
}

.foundations__block {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.foundations__head {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.foundations__title {
  color: var(--color-text-light);
  text-transform: uppercase;
}

.foundations__note {
  max-width: 78ch;
  margin: 0;
  color: var(--color-text-secondary);
}

.foundations__grid {
  display: grid;
  gap: var(--space-03);
  margin: 0;
  padding: 0;
  list-style: none;
}

.foundations__grid--swatch {
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
}

.foundations__grid--tile {
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
}

.foundations__cell {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  min-width: 0;
}

.foundations__rows {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  margin: 0;
  padding: 0;
  list-style: none;
}

/* Bar, then name, then value — the bar column is fixed at the widest step so
   the names line up and the steps read as a scale rather than a list. */
.foundations__row {
  display: grid;
  grid-template-columns: 96px 220px minmax(0, 1fr);
  align-items: center;
  gap: var(--space-03);
}

.foundations__row--plain {
  grid-template-columns: 220px minmax(0, 1fr);
}

.foundations__bar {
  height: var(--space-03);
  background: var(--color-brand);
  border-radius: var(--radius-01);
}

.foundations__name {
  margin: 0;
  color: var(--color-text);
}

.foundations__value {
  margin: 0;
  overflow: hidden;
  color: var(--color-text-light);
  white-space: nowrap;
  text-overflow: ellipsis;
}

.foundations__mobile {
  font-style: normal;
  color: var(--color-text-secondary);
}

/* Over the blur backdrop the two light text tones disappear. */
.foundations__name--over,
.foundations__value--over {
  color: var(--color-text-contrast);
}

/* The split backing: half neutral, half contrast, so a token carrying alpha
   shows what it actually does on both of the app's surfaces. */
.swatch {
  display: block;
  height: var(--space-08);
  overflow: hidden;
  background: linear-gradient(
    90deg,
    var(--color-background-neutral) 0 50%,
    var(--color-background-contrast) 50% 100%
  );
  border: 1px solid var(--color-border);
  border-radius: var(--radius-02);
}

.swatch__fill {
  display: block;
  width: 100%;
  height: 100%;
}

.corner {
  display: block;
  height: var(--space-08);
  background: var(--color-brand);
}

.cast {
  display: block;
  height: var(--space-08);
  background: var(--color-background-neutral);
  border-radius: var(--radius-03);
}

/* A smooth gradient blurs to itself: 4 px and 64 px would draw the same tile
   and the scale would read as broken. The stripes are the high-frequency detail
   a blur radius actually consumes, and the colour underneath is what makes the
   larger radii still visibly different from each other. Palette tokens
   throughout, so it moves with the palette. */
.backdrop {
  padding: var(--space-04);
  background:
    repeating-linear-gradient(
      45deg,
      var(--color-background-neutral-50) 0 8px,
      transparent 8px 20px
    ),
    radial-gradient(circle at 20% 30%, var(--color-brand) 0%, transparent 45%),
    radial-gradient(circle at 75% 70%, var(--color-success) 0%, transparent 40%),
    linear-gradient(120deg, var(--color-background-contrast), var(--color-text-secondary));
  border-radius: var(--radius-03);
}

.pane {
  display: block;
  height: var(--space-08);
  background: var(--color-background-neutral-12);
  border-radius: var(--radius-02);
}

/* No background of its own, deliberately: .glass-surface brings one, and a
   scoped rule would outrank it (an attribute selector on top of the class).
   Only the size is ours — and the radius, which has to match the one
   .glass-border draws its stroke at. */
.glass-tile {
  --glass-radius: var(--radius-05);

  display: block;
  height: var(--space-08);
  border-radius: var(--radius-05);
}

.foundations__rows--specimen {
  gap: var(--space-04);
}

.foundations__specimen {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  padding-bottom: var(--space-03);
  border-bottom: 1px solid var(--color-border);
}

.foundations__sample {
  margin: 0;
  color: var(--color-text);
}

/* The three-column rows stop fitting long before the pane does: the value drops
   to a line of its own and the scale keeps its alignment. */
@media (max-width: 900px) {
  .foundations__row {
    grid-template-columns: 72px minmax(0, 1fr);
  }

  .foundations__row--plain {
    grid-template-columns: minmax(0, 1fr);
  }

  .foundations__row .foundations__value {
    grid-column: 1 / -1;
  }
}
</style>
