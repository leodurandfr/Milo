<!-- frontend/src/components/gallery/GalleryVariant.vue -->
<template>
  <div class="gallery-variant">
    <span v-if="label" class="gallery-variant__label text-mono-small">{{ label }}</span>
    <div
      class="gallery-variant__row"
      :class="{ 'gallery-variant__row--stacked': stacked, 'gallery-variant__row--contained': contain }"
      :style="contain ? { height: `${containHeight}px` } : null"
    >
      <slot />
    </div>
  </div>
</template>

<script setup>
defineProps({
  /** What is being varied, in prop syntax — `variant="brand"`, `disabled`, … */
  label: {
    type: String,
    default: ''
  },
  /** Lay the samples out in a column: full-width controls, rows, sliders. */
  stacked: {
    type: Boolean,
    default: false
  },
  /**
   * For a `position: fixed` primitive (Logo, NotificationBanner). A transformed
   * element is the containing block of its fixed descendants, so this keeps the
   * sample inside its card instead of anchoring it to the viewport — the
   * component's own offsets still resolve, just against this box.
   */
  contain: {
    type: Boolean,
    default: false
  },
  /** Height of the `contain` box, in px — a fixed child contributes no layout. */
  containHeight: {
    type: Number,
    default: 120
  }
});
</script>

<style scoped>
.gallery-variant {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.gallery-variant__label {
  color: var(--color-text-light);
}

.gallery-variant__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-02);
}

.gallery-variant__row--stacked {
  flex-direction: column;
  align-items: stretch;
}

.gallery-variant__row--contained {
  position: relative;
  overflow: hidden;
  background: var(--color-background-strong);
  border-radius: var(--radius-03);

  /* The identity transform is the point — see the `contain` prop. */
  transform: translate(0);
}
</style>
