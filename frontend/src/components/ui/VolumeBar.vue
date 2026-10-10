<!-- frontend/src/components/ui/VolumeBar.vue -->
<template>
  <div
    class="volume-bar glass-shell"
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
import { useI18n } from '@/services/i18n';

defineProps({
  // The tone of the ground the bar is drawn over, not the tone of the bar
  // itself — same sense as ProgressBar's. "on-contrast" is any dark ground: the
  // dark theme, and the Lyrics view in either theme, where the strong fill
  // would sink into the backdrop. App.vue picks it from useDarkSurface(), so
  // nothing here has to know which surfaces those are.
  variant: {
    type: String,
    default: 'default',
    validator: (v) => ['default', 'on-contrast'].includes(v)
  }
});

const unifiedStore = useUnifiedAudioStore();
const { formatUnit } = useI18n();

const volumeDisplay = computed(() => formatUnit(Math.round(unifiedStore.volumeState.global_volume_db), 'dB'));

// The server's own 0..1 over the limits it applied — the span the level was
// measured on travels with it, so the fill never mixes a new level with old
// limits. Between two echoes the fill's transition does the smoothing.
const volumeFillStyle = computed(() => ({
  width: '100%',
  transform: `translateX(${unifiedStore.volumeState.global_volume * 100 - 100}%)`
}));
</script>

<style scoped>
.volume-bar {
  top: calc(env(safe-area-inset-top,0px) + var(--space-05));
  position: fixed;
  left: 50%;
  transform: translate(-50%, -80px);
  opacity: 0;
  width: 472px;
  padding: var(--space-04);
  border-radius: var(--radius-full);
  transition: transform var(--transition-spring-snappy), opacity var(--transition-normal);
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
   Three layers flip, and on a contrast surface the plate takes the glass's
   dark tone. The fill takes the far end of
   the ramp — near-black on light, light on dark — and carries the contrast on
   its own, so the track only has to hint at how far the value has travelled: on
   dark that is a second coat of the plate's own wash, about half the step the
   default variant needs to register against its pale backdrop. An ink track
   was tried here instead (the contrast surface at 32%), which sinks the plate
   into a well the way the default variant's does and holds its contrast
   against a brighter backdrop; it was turned down on looks. The readout is the muted tone
   of whichever end the fill sits at, so it reads on the fill — where the value
   spends most of its travel — exactly as it does in the other: the tertiary on
   the near-black fill, and on the white fill the light secondary in both
   themes (color-scheme: light below), the dark one being too light for it.
   Near zero the readout leaves the fill for the track, and holds no ratio there. */

.volume-bar--default {
  --volume-track: var(--color-glass-strong);
  --volume-fill: var(--color-fill);
  --volume-text: var(--color-text-tertiary);
}

.volume-bar--on-contrast {
  --volume-track: var(--color-glass);
  --volume-fill: var(--color-text-on-contrast);
  --volume-text: var(--color-text-secondary);
  --glass-tone: var(--color-shell-on-contrast);
}

.volume-bar--on-contrast .text-mono-medium {
  color-scheme: light;
}

@media (max-aspect-ratio: 4/3) {
  /* Lands exactly on AudioSourceLayout's navigation header: same side inset,
     same top, and the same 64px height. */
  .volume-bar {
    --volume-bar-top: calc(max(var(--space-05-fixed), env(safe-area-inset-top, 0px)) + var(--space-02));
    width: calc(100% - 2 * var(--space-05));
    top: var(--volume-bar-top);
    /* Hidden fully above the screen edge whatever the safe-area inset adds to
       the top — a fixed -80px left part of the bar on screen under a notch. */
    transform: translate(-50%, calc(-100% - var(--volume-bar-top)));
  }

  .volume-bar.visible {
    transform: translate(-50%, 0);
  }
}
</style>
