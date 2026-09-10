<!-- frontend/src/components/ui/VolumeBar.vue -->
<template>
  <div
    class="volume-bar glass-surface glass-border"
    :class="[`volume-bar--${variant}`, { visible: unifiedStore.showVolumeBar }]"
    @click="unifiedStore.hideVolumeBar()"
  >
    <div class="volume-slider">
      <div class="volume-fill" :style="volumeFillStyle"></div>
      <div class="text-mono-medium">{{ volumeDisplay }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useSettingsStore } from '@/stores/settingsStore';

defineProps({
  // The tone of the surface the bar is drawn on, not the tone of the bar itself
  // — same sense as ProgressBar's. "light" is the whole app; "dark" is the
  // screensaver and the Lyrics view, where the near-black fill would sink into
  // the backdrop. App.vue picks it from useDarkSurface(), so nothing here has to
  // know which views those are.
  variant: {
    type: String,
    default: 'light',
    validator: (v) => ['light', 'dark'].includes(v)
  }
});

const unifiedStore = useUnifiedAudioStore();
const settingsStore = useSettingsStore();

// Volume in dB (average of unmuted clients)
const volumeDb = computed(() => unifiedStore.volumeState.global_volume_db);

// Volume limits from settings
const limitMin = computed(() => settingsStore.volumeLimits.min_db);
const limitMax = computed(() => settingsStore.volumeLimits.max_db);

const volumeDisplay = computed(() => `${Math.round(volumeDb.value)} dB`);

// Fill percentage interpolated on volume limits (limit_min = 0%, limit_max = 100%)
const fillPercent = computed(() => {
  const range = limitMax.value - limitMin.value;
  if (range <= 0) return 0;
  return ((volumeDb.value - limitMin.value) / range) * 100;
});

const volumeFillStyle = computed(() => ({
  width: '100%',
  transform: `translateX(${fillPercent.value - 100}%)`
}));
</script>

<style scoped>
.volume-bar {
  top: calc(env(safe-area-inset-top,0px) + var(--space-05));
  /* The plate is the one layer both variants share: a mid grey wash lifts it off
     a dark backdrop exactly as it settles it into a light one. */
  --glass-bg: var(--color-background-medium-16);
  --glass-radius: var(--radius-full);
  --glass-stroke-width: 1px;
  position: fixed;
  left: 50%;
  transform: translate(-50%, -80px);
  opacity: 0;
  width: 472px;
  padding: var(--space-04);
  border-radius: var(--radius-full);
  transition: all var(--transition-spring-snappy);
  z-index: 8000;
  /* Only intercept taps while fully shown: during the fade-out the bar lets
     clicks pass through, so re-tapping never interrupts the disappear animation. */
  pointer-events: none;
}

.volume-bar.visible {
  opacity: 1;
  transform: translate(-50%, 0);
  left: 50%;
  pointer-events: auto;
  cursor: pointer;
}

.volume-slider {
  position: relative;
  width: 100%;
  height: 32px;
  border-radius: var(--radius-full);
  overflow: hidden;
}

.volume-slider::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0.5px;
  right: 0.5px;
  height: 100%;
  background: var(--volume-track);
  border-radius: var(--radius-full);
  z-index: 0;
}

.volume-slider .text-mono-medium {
  height: 100%;
  align-content: center;
  color: var(--volume-text);
  margin-left: var(--space-04);
  position: absolute;
  z-index: 2;
}

.volume-fill {
  position: absolute;
  height: 100%;
  background: var(--volume-fill);
  border-radius: var(--radius-full);
  transition: transform var(--transition-fast);
  z-index: 1;
}

/* === Variants ===
   Three layers flip; the plate above is shared. The fill takes the far end of
   the ramp — near-black on light, white on dark — and carries the contrast on
   its own, so the track only has to hint at how far the value has travelled: on
   dark that is a second coat of the plate's own wash, about half the step the
   light variant needs to register against its pale backdrop. An ink track was
   tried here instead (--color-background-contrast-32), which sinks the plate
   into a well the way the light variant's does and holds its contrast against a
   brighter backdrop; it was turned down on looks. The readout is the muted tone
   of whichever end the fill sits at, so it reads on the fill — where the value
   spends most of its travel — exactly as it does in the other. */

.volume-bar--light {
  --volume-track: var(--color-background-medium-32);
  --volume-fill: var(--color-background-contrast);
  --volume-text: var(--color-text-light);
}

.volume-bar--dark {
  --volume-track: var(--color-background-medium-16);
  --volume-fill: var(--color-background-neutral);
  --volume-text: var(--color-text-secondary);
}

@media (max-aspect-ratio: 4/3) {
  .volume-bar {
    width: calc(100% - 2*(var(--space-04)));
    top: max(var(--space-05), env(safe-area-inset-top, 0px));
  }
}
</style>
