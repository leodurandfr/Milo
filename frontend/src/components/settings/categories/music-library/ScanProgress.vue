<!-- frontend/src/components/settings/categories/music-library/ScanProgress.vue -->
<!--
  Navidrome scan progress bar — shared by the maintenance "Library refresh"
  section and the add-share wizard's post-connect indexing screen, so both
  render the same reveal/bar/indeterminate-sweep visuals from one place.
-->
<template>
  <div class="scan-progress" :class="{ 'is-open': open }">
    <div class="scan-progress__inner">
      <div v-if="hasBar" class="scan-progress__track" :class="{ 'is-indeterminate': indeterminate }">
        <div class="scan-progress__fill" :style="fillStyle" />
      </div>
      <span class="scan-progress__label text-mono-small">{{ label }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';

const props = defineProps({
  open: { type: Boolean, default: false },
  hasBar: { type: Boolean, default: true },
  indeterminate: { type: Boolean, default: false },
  pct: { type: Number, default: 0 },
  label: { type: String, required: true },
});

const fillStyle = computed(() => (props.indeterminate ? {} : { width: `${props.pct}%` }));
</script>

<style scoped>
/* Reveal — grid-rows + opacity + margin; the negative margin cancels the card's
   row gap while collapsed. */
.scan-progress {
  display: grid;
  grid-template-rows: 0fr;
  opacity: 0;
  margin-top: calc(-1 * var(--space-04));
  transition:
    grid-template-rows var(--transition-fast),
    opacity var(--transition-fast),
    margin-top var(--transition-fast);
}

.scan-progress.is-open {
  grid-template-rows: 1fr;
  opacity: 1;
  margin-top: 0;
}

.scan-progress__inner {
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.scan-progress__track {
  height: 8px;
  background: var(--color-background-strong);
  border-radius: var(--radius-01);
  overflow: hidden;
}

.scan-progress__fill {
  height: 100%;
  background: var(--color-background-contrast);
  border-radius: var(--radius-01);
  transition: width var(--transition-medium);
}

/* Indeterminate variant (no known total): a segment sweeps instead of filling. */
.scan-progress__track.is-indeterminate .scan-progress__fill {
  width: 40%;
  animation: scan-progress-indeterminate 1.1s ease-in-out infinite;
}

@keyframes scan-progress-indeterminate {
  0% {
    transform: translateX(-120%);
  }

  100% {
    transform: translateX(280%);
  }
}

.scan-progress__label {
  color: var(--color-text-secondary);
}
</style>
