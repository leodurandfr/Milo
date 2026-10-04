<!-- PlayerTransport.vue - The transport of a source, in both players: shuffle,
     the steps around the main button, repeat — each drawn iff the state lists
     its command (utils/playerControls, read through the player's usePlayerState),
     in the same order, glyphs, rungs and inks wherever it is drawn. PlayerBody
     draws it in both players: on the full player's plate, and on the playing
     bar's card. Only the surface changes: the plate's light ground, or the
     card's dark one.
     A relative skip (−15 / +30) is emitted, not sent: the progress bar beside
     the transport owns the playhead that shows a burst's sum at once.
     The `end` slot is what the source adds after the row that is not a command
     (radio's favorite): it takes an end the way a toggle does, held by a spacer
     at the other one, so the main button stays centred. -->
<template>
  <!-- The plate carries its own scale; on the card the bar's compact one
       applies. In the template, where the icon-scale guardrail reads it. -->
  <div class="player-transport" :class="[`player-transport--${surface}`,
    surface === 'card' ? null : isMobile ? 'transport-scale--phone' : 'transport-scale',
    { 'player-transport--toggles': hasToggles || !!$slots.end }]" @click.stop>
    <span v-if="$slots.end" class="player-button player-button--toggle player-extra" aria-hidden="true" />
    <template v-for="control in plate" :key="control.id">
      <span v-if="control.spacer" class="player-button player-button--toggle player-extra"
        aria-hidden="true" />
      <IconButton v-else :icon="control.icon" variant="ghost"
        :size="control.id === 'main' ? 'medium' : 'small'"
        :color="ink(control)"
        class="player-button"
        :class="[control.id === 'main' ? 'player-button--primary transport-primary'
          : isToggle(control) || control.command === 'skip' ? 'transport-secondary-round player-extra'
            : 'transport-secondary player-extra', { 'player-button--toggle': isToggle(control) }]"
        :aria-label="isToggle(control) ? toggleLabel(control) : undefined"
        :aria-pressed="isToggle(control) ? control.active : undefined"
        :loading="control.id === 'main' && isBuffering" :disabled="!control.enabled"
        @click="press(control)" />
    </template>
    <span v-if="$slots.end" class="player-button player-button--toggle player-extra player-transport-end">
      <slot name="end" :ink="INKS[surface]" />
    </span>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useIsMobile } from '@/composables/useIsMobile';
import { usePlayerState } from '@/composables/usePlayerState';
import IconButton from '@/components/ui/IconButton.vue';

const props = defineProps({
  source: {
    type: String,
    required: true
  },
  // What it is drawn on: the full player's plate (light ground, its own
  // transport scale), or the playing bar's card (dark ground, the bar's scale).
  surface: {
    type: String,
    default: 'plate',
    validator: (value) => ['plate', 'card'].includes(value)
  }
});

const emit = defineEmits(['skip']);

const { t } = useI18n();
const { isMobile } = useIsMobile();
// The player's one reading of its state, made by the shell drawing this row.
const { controls, shownControls, isBuffering, sendSourceCommand } = usePlayerState(props.source).controls;

const isToggle = (control) => control.id === 'shuffle' || control.id === 'repeat';
const transportControls = computed(() => shownControls.value.filter(control => control.row === 'transport'));
const hasToggles = computed(() => transportControls.value.some(isToggle));

// The row in drawing order, its two ends held by a spacer when only one toggle
// is listed, so the main button stays centred.
const plate = computed(() => {
  const steps = transportControls.value.filter(control => !isToggle(control));
  if (!hasToggles.value) return steps;
  const find = (id) => transportControls.value.find(control => control.id === id);
  return [
    find('shuffle') ?? { id: 'spacer-start', spacer: true },
    ...steps,
    find('repeat') ?? { id: 'spacer-end', spacer: true }
  ];
});


// The main button and a toggle that is on take the strong ink of the surface;
// the steps and a toggle that is off the lighter one.
const INKS = {
  plate: { strong: 'var(--color-text)', light: 'var(--color-text-tertiary)' },
  card: { strong: 'var(--color-text-on-contrast)', light: 'var(--color-text-on-contrast-secondary)' }
};

function ink(control) {
  const tone = INKS[props.surface];
  return control.id === 'main' || control.active ? tone.strong : tone.light;
}

// Classes, in the template so the icon-scale guardrail sees them: the flanking
// glyphs fill their box in both axes or not, which sets their rung
// (design-system.css § transport roles), and everything but the main button
// is `player-extra`, which the phone's mini-bar hides.

const REPEAT_LABEL_KEYS = {
  off: 'player.repeatOff',
  context: 'player.repeatContext',
  track: 'player.repeatTrack',
};

function toggleLabel(control) {
  if (control.id === 'repeat') return t(REPEAT_LABEL_KEYS[control.mode]);
  return t('player.shuffle');
}

function press(control) {
  if (control.command === 'skip') {
    if (controls.value.includes('skip')) emit('skip', control.seconds);
    return;
  }
  sendSourceCommand(control.command, control.params);
}
</script>

<style scoped>
.player-transport {
  display: flex;
  align-items: center;
}

/* === PLATE (AudioPlayerFull) ===
   space-evenly splits what the plate's padding leaves, so the side padding is
   the one knob that sets the whole rhythm: widening it tightens the buttons and
   opens the ends in the same move. --space-04 left the gaps between the three
   icons of a prev/play/next plate slightly *wider* than the margins framing
   them (105 against 100 on the 528px desktop plate, 66 against 59 on the
   phone's 356px one), which read as three loose icons rather than one control.
   --space-06 puts 86.5px between glyph edges against 101.5 at the ends on the
   desktop plate, and 55.5 against 58.5 on the phone's. The token carries its
   own mobile step (32 desktop, 24 phone), which is what keeps the narrower
   plate from running out of room. */
.player-transport--plate {
  color: var(--color-text);
  background: var(--color-inset);
  border-radius: var(--radius-06);
  justify-content: space-evenly;
  padding: var(--space-01) var(--space-06);
}

/* The tap target, which is NOT the icon and does not follow it: 80/90px circles
   sized for a finger on the kiosk. IconButton sizes itself from its padding, so
   without these the buttons would collapse to the icon plus 8px. */
.player-transport--plate .player-button {
  width: 80px;
  height: 80px;
  padding: 0;
  border-radius: 50%;
}

.player-transport--plate .player-button--primary {
  width: 90px;
  height: 90px;
}

/* Five on the plate: shuffle and repeat at the ends take a smaller target, and
   the steps and the main button give up some of theirs, so the row still fits
   the kiosk's column at a 115% interface scale (385px) and a 390px phone. */
.player-transport--plate.player-transport--toggles {
  padding: var(--space-01) var(--space-04);
}

.player-transport--plate.player-transport--toggles .player-button {
  width: 64px;
  height: 64px;
}

.player-transport--plate.player-transport--toggles .player-button--primary {
  width: 80px;
  height: 80px;
}

.player-transport--plate .player-button.player-button--toggle {
  flex-shrink: 0;
  width: 56px;
  height: 56px;
}

/* The source's own button at the row's end, in the box a toggle takes. */
.player-transport-end {
  display: flex;
  align-items: center;
  justify-content: center;
}

/* A loading ghost dims the ink it inherits; on the plate the spinner keeps
   the icon's full tone instead. */
.player-transport--plate .player-button--primary.icon-button--loading {
  color: var(--color-text);
}

/* === CARD (AudioPlayer, through PlayerBody) ===
   The bar's own ghost buttons, at its compact scale: the trio centred, and
   once a toggle is listed, shuffle and repeat pushed to the row's two ends. */
.player-transport--card {
  color: var(--color-text-on-contrast);
  justify-content: center;
  gap: var(--space-01);
  width: 100%;
}

.player-transport--card.player-transport--toggles {
  justify-content: space-between;
}

/* The spacer holds the end a missing toggle would take: a ghost button is its
   glyph plus the ghost padding on each side. */
.player-transport--card .player-button--toggle:not(.icon-button) {
  width: calc(var(--transport-secondary-round) + 2 * var(--space-02));
  height: calc(var(--transport-secondary-round) + 2 * var(--space-02));
  flex-shrink: 0;
}

@media (max-aspect-ratio: 4/3) {
  .player-transport--plate .player-button.player-button--toggle {
    width: 48px;
    height: 48px;
  }

  /* The mini-bar keeps the main button alone (AudioPlayer hides the extras):
     nothing is left for the ends to hold. */
  .player-transport--card,
  .player-transport--card.player-transport--toggles {
    justify-content: center;
  }
}
</style>
