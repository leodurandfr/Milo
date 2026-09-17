<!-- frontend/src/components/settings/ProgressStrip.vue -->
<!--
  The one progress strip the settings screens share: Navidrome's library scan,
  the add-share wizard's post-connect indexing, and the multiroom analysis.

  Two modes, one design. `percent` null sweeps, for work whose total is unknown
  — Navidrome never reports a scan's target count. A number fills, for work
  whose duration the backend can state. The second exists because the multiroom
  analysis knows how long it takes, and drawing it a second bar of its own is
  how two progress indicators come to look different in one app.
-->
<template>
  <div class="scan-progress" :class="{ 'is-open': open }">
    <div class="scan-progress__inner">
      <div v-if="hasBar" class="scan-progress__track">
        <div class="scan-progress__fill"
          :class="{ 'scan-progress__fill--sweeping': percent === null }"
          :style="percent === null ? null : { width: `${percent}%`, '--step-ms': `${stepMs}ms` }" />
      </div>
      <span class="scan-progress__labels text-mono-small">
        <span class="scan-progress__label">{{ label }}</span>
        <span v-if="hint" class="scan-progress__hint">{{ hint }}</span>
      </span>
    </div>
  </div>
</template>

<script setup>
defineProps({
  open: { type: Boolean, default: false },
  hasBar: { type: Boolean, default: true },
  label: { type: String, required: true },
  /** null sweeps (unknown total); a number fills that share of the track. */
  percent: { type: Number, default: null },
  /** Right-aligned counterpart to `label` — a countdown, a count, a ratio. */
  hint: { type: String, default: '' },
  /**
   * How long a determinate fill takes to reach a new width, in ms. It must
   * match the caller's update interval or the bar steps visibly: a 250 ms
   * animation fed every second spends three quarters of its time still.
   */
  stepMs: { type: Number, default: 200 },
});
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
  width: 0;
  height: 100%;
  background: var(--color-background-contrast);
  border-radius: var(--radius-01);
  transition: width var(--step-ms, 200ms) linear;
}

/* A segment sweeps across the track when there is no known total to fill toward. */
.scan-progress__fill--sweeping {
  width: 40%;
  transition: none;
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

.scan-progress__labels {
  display: flex;
  justify-content: space-between;
  gap: var(--space-02);
  color: var(--color-text-secondary);
}

.scan-progress__hint {
  white-space: nowrap;
}
</style>
