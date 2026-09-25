<template>
  <div class="controls" :class="isMobile ? 'transport-scale--phone' : 'transport-scale'">
    <IconButton icon="previous" variant="ghost" size="small" color="var(--color-text-light)"
      class="control-button transport-secondary" :disabled="!hasPrev" @click="$emit('previous')" />
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
  //
  // It also blocks the button, which the hand-rolled version this component
  // replaced did not. That is deliberate rather than incidental: Music Library,
  // podcast and radio all already pass isBuffering to IconButton's `loading` and
  // have always been inert during the wait, so the odd one out was here. A
  // command sent mid-`start()` reaches a source that has not finished
  // transitioning, and the state machine drops updates while `transitioning` is
  // set — the press would look accepted and do nothing.
  isBuffering: {
    type: Boolean,
    default: false
  },
  // Whether the source takes `prev` / `next` now: `next` is absent on the
  // last track of a disc or a queue. Default true for callers with no such state.
  hasPrev: {
    type: Boolean,
    default: true
  },
  hasNext: {
    type: Boolean,
    default: true
  }
});

defineEmits(['play-pause', 'previous', 'next']);

const { isMobile } = useIsMobile();
</script>

<style scoped>
/* space-evenly splits what the plate's padding leaves, so the side padding is
   the one knob that sets the whole rhythm: widening it tightens the three
   buttons and opens the ends in the same move. --space-04 left both plates with
   the gaps between the icons slightly *wider* than the margins framing them
   (105 against 100 on the 528px desktop plate, 66 against 59 on the phone's
   356px one), which read as three loose icons rather than one control.
   --space-06 puts 86.5px between glyph edges against 101.5 at the ends on the
   desktop plate, and 55.5 against 58.5 on the phone's. The token carries its own
   mobile step (32 desktop, 24 phone), which is what keeps the narrower plate
   from running out of room. */
.controls {
  background: var(--color-background);
  border-radius: var(--radius-06);
  display: flex;
  justify-content: space-evenly;
  align-items: center;
  padding: var(--space-01) var(--space-06);
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
