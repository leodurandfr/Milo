<template>
  <div class="controls" :class="isMobile ? 'transport-scale--compact' : 'transport-scale'">
    <IconButton icon="previous" variant="ghost" size="small" color="var(--color-text-light)"
      class="control-button transport-secondary" @click="$emit('previous')" />
    <IconButton :icon="isPlaying ? 'pause' : 'play'" variant="ghost" size="medium"
      color="var(--color-text)" class="control-button control-button--primary transport-primary"
      :loading="isBuffering" @click="$emit('play-pause')" />
    <IconButton icon="next" variant="ghost" size="small" color="var(--color-text-light)"
      class="control-button transport-secondary" :disabled="!hasNext" @click="$emit('next')" />
  </div>
</template>

<script setup>
import IconButton from '@/components/ui/IconButton.vue';
import { useIsMobile } from '@/composables/useIsMobile';

defineProps({
  isPlaying: {
    type: Boolean,
    default: false
  },
  // Source is spinning up / buffering (e.g. CD drive starting): show a spinner
  // in place of the play/pause icon until audio actually flows.
  isBuffering: {
    type: Boolean,
    default: false
  },
  // Default true: sources with no "last track" concept stay unaffected.
  hasNext: {
    type: Boolean,
    default: true
  }
});

defineEmits(['play-pause', 'previous', 'next']);

const { isMobile } = useIsMobile();
</script>

<style scoped>
.controls {
  background: var(--color-background);
  border-radius: var(--radius-06);
  display: flex;
  justify-content: space-evenly;
  align-items: center;
  padding: var(--space-01) var(--space-04);
}

/* The tap target, which is NOT the icon and does not follow it: 80/90px circles
   sized for a finger on the kiosk. IconButton sizes itself from its padding, so
   without these the buttons would collapse to the icon plus 8px. */
.controls .control-button {
  width: 80px;
  height: 80px;
  padding: 0;
  border-radius: 50%;
  color: var(--color-text-light);
}

.controls .control-button--primary {
  width: 90px;
  height: 90px;
  color: var(--color-text);
}

/* The ghost variant assumes a dark ground and dims its own colour while
   loading; this row sits on --color-background, so the spinner keeps the icon's
   tone instead. */
.controls .control-button--primary.icon-button--loading {
  color: var(--color-text);
}
</style>
