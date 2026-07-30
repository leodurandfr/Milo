<!-- frontend/src/components/gallery/GalleryItem.vue -->
<template>
  <article v-if="isShown" class="gallery-item">
    <header class="gallery-item__head">
      <h3 class="heading-4">{{ entry.id }}</h3>
      <span v-if="entry.coupling" class="gallery-item__badge text-mono-small">{{ entry.coupling }}</span>
      <code class="gallery-item__path text-mono-small">{{ entry.file }}</code>
    </header>

    <p class="gallery-item__summary text-mono-small">{{ entry.summary }}</p>

    <div v-if="!hasStage" class="gallery-item__note text-mono-small">
      No variants grid for this one — a fixed-position, store-driven primitive has no
      side-by-side form. Drive it from the Playground tab instead.
    </div>
    <div v-else class="gallery-item__stage">
      <slot />
    </div>
  </article>
</template>

<script setup>
import { computed, inject, useSlots } from 'vue';
import { entryById } from './catalog';

const props = defineProps({
  /** Catalogue id — the component's own name. */
  id: {
    type: String,
    required: true
  }
});

/**
 * The Variants tab renders a whole group's demo but shows one primitive, so the
 * page provides the selected id and each card decides whether it is wanted. That
 * is what lets the five per-group demo files stay as they are instead of being
 * split into twenty-three. Absent when nobody provides it — then every card
 * shows, which is what a plain group listing would want.
 */
const selectedId = inject('gallerySelectedId', null);

const isShown = computed(() => {
  const wanted = selectedId?.value;
  return !wanted || wanted === props.id;
});

// A demo naming an id the catalogue does not carry is a mistake worth seeing on
// screen rather than rendering as a blank card.
const entry = computed(() => entryById(props.id) ?? {
  id: props.id,
  file: 'unknown — missing from catalog.js',
  summary: ''
});

/**
 * Whether this card has anything to show. Read from the slot rather than a
 * catalogue flag: a demo that passes no content is stating exactly that, and a
 * flag saying the same thing in a second place is one more thing to keep true.
 */
const slots = useSlots();
const hasStage = computed(() => !!slots.default);
</script>

<style scoped>
.gallery-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  padding: var(--space-04);
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  box-shadow: var(--shadow-02);
}

.gallery-item__head {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--space-02);
}

.gallery-item__badge {
  padding: 0 var(--space-01);
  color: var(--color-warning);
  background: var(--color-warning-subtle);
  border-radius: var(--radius-01);
}

.gallery-item__path {
  color: var(--color-text-light);
}

.gallery-item__summary {
  margin: 0;
  color: var(--color-text-secondary);
}

.gallery-item__note {
  padding: var(--space-03);
  color: var(--color-text-secondary);
  background: var(--color-background-strong);
  border-radius: var(--radius-03);
}

.gallery-item__stage {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
  padding: var(--space-04);
  margin-top: var(--space-02);
  background: var(--color-background);
  border-radius: var(--radius-03);
}
</style>
