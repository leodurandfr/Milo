<!-- PlayerTransport.vue - The transport of a source, in both players: shuffle,
     the steps around the main button, repeat — each drawn iff the state lists
     its command (utils/playerControls, read through the player's usePlayerState),
     in the same order, glyphs, rungs and inks wherever it is drawn. PlayerBody
     draws it in both players: on the full player's plate, and on the playing
     bar's card. Neither draws a ground of its own; only the ink and the tap
     targets change: the plate's theme ink and finger-sized targets, or the
     card's ink on its dark glass and the bar's ghost buttons.
     A relative skip (−15 / +30) is emitted, not sent: the progress bar beside
     the transport owns the playhead that shows a burst's sum at once.
     The steps and the main button sit together as one control; the toggles
     are pushed to the row's two ends, their glyphs on the progress bar's.
     Without toggles, the three buttons spread a little: nothing flanks them
     to tell them apart from.
     The `end` slot is what the source adds after the row that is not a command
     (radio's favorite): it takes an end the way a toggle does, held by a spacer
     at the other one, so the main button stays centred — or, beside a labelled
     main button that fills the row, a button of the same fill at its end. -->
<template>
  <!-- The plate carries its own scale; on the card the bar's compact one
       applies. In the template, where the icon-scale guardrail reads it. -->
  <div class="player-transport" :class="[`player-transport--${surface}`,
    surface === 'card' ? null : isMobile ? 'transport-scale--phone' : 'transport-scale']" @click.stop>
    <!-- The transport and a labelled button that stands for it (a
         take-over) cross-fade in the same box: the row keeps its height. -->
    <Transition name="transport-swap" mode="out-in">
      <!-- The row carries its own layout, so the one leaving keeps it while
           it fades out rather than re-flowing to the one arriving. -->
      <div :key="labelled ? 'labelled' : 'controls'" class="player-transport-row"
        :class="{ 'player-transport--toggles': hasToggles || (!!$slots.end && !labelled),
                  'player-transport--labelled-end': labelled && !!$slots.end }">
        <span v-if="$slots.end && !labelled" class="player-button player-button--toggle player-extra"
          :class="{ 'player-transport-edge--start': !hasToggles }" aria-hidden="true" />
        <template v-for="control in plate" :key="control.id">
          <span v-if="control.spacer" class="player-button player-button--toggle player-extra"
            :class="`player-transport-edge--${control.edge}`" aria-hidden="true" />
          <!-- In a box the height of the main button it stands for, so the row
               does not jump when the transport comes back. -->
          <span v-else-if="isLabelled(control)" class="player-transport-labelled">
            <Button :variant="filled" size="medium" :left-icon="control.icon"
              class="player-button--labelled" :loading="pending === control.command || (control.id === 'main' && isBuffering)" :disabled="!control.enabled"
              @click="press(control)">
              {{ t(LABEL_KEYS[control.command]) }}
            </Button>
          </span>
          <IconButton v-else :icon="control.icon" variant="ghost"
            :size="control.id === 'main' ? 'medium' : 'small'"
            :color="ink(control)"
            class="player-button"
            :class="[control.id === 'main' ? 'player-button--primary transport-primary'
              : isToggle(control) ? 'transport-toggle player-extra' : 'transport-secondary player-extra', { 'player-button--toggle': isToggle(control) },
              control.edge ? `player-transport-edge--${control.edge}` : null]"
            :aria-label="isToggle(control) ? toggleLabel(control) : undefined"
            :aria-pressed="isToggle(control) ? control.active : undefined"
            :loading="control.id === 'main' && isBuffering" :disabled="!control.enabled"
            @click="press(control)" />
        </template>
        <span v-if="$slots.end" class="player-extra player-transport-end"
          :class="labelled ? null
            : ['player-button', 'player-button--toggle', { 'player-transport-edge--end': !hasToggles }]">
          <slot name="end" :variant="labelled ? filled : 'ghost'" />
        </span>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useIsMobile } from '@/composables/useIsMobile';
import { useTimer } from '@/composables/useTimer';
import { usePlayerState } from '@/composables/usePlayerState';
import Button from '@/components/ui/Button.vue';
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
// A control that says what it does in words — unless its glyph can say it
// alone and there is no room for words: the phone's mini-bar, where a live
// stream's stop stays its glyph. A take-over keeps its words everywhere: its
// bare play glyph would read as playing it on the other device.
const isLabelled = (control) => control.labelled
  && !(control.glyphSuffices && props.surface === 'card' && isMobile.value);
const labelled = computed(() => transportControls.value.some(isLabelled));

// The row in drawing order, its two ends held by a spacer when only one toggle
// is listed, so the main button stays centred. `edge` marks what is pushed
// away from the steps.
const plate = computed(() => {
  const steps = transportControls.value.filter(control => !isToggle(control));
  if (!hasToggles.value) return steps;
  const find = (id) => transportControls.value.find(control => control.id === id);
  return [
    { ...(find('shuffle') ?? { id: 'spacer-start', spacer: true }), edge: 'start' },
    ...steps,
    { ...(find('repeat') ?? { id: 'spacer-end', spacer: true }), edge: 'end' }
  ];
});

// What a button with a ground takes on each surface: a labelled main button,
// and the end slot's button beside it.
const filled = computed(() => (props.surface === 'card' ? 'on-contrast' : 'control'));

// The main button and the steps beside it are one control in one ink, the
// strong one of the surface; a toggle takes it when on, the lighter one when
// off — so an idle toggle never reads as a step.
const INKS = {
  plate: { strong: 'var(--color-text)', light: 'var(--color-text-tertiary)' },
  card: { strong: 'var(--color-text-on-contrast)', light: 'var(--color-text-on-contrast-secondary)' }
};

function ink(control) {
  const tone = INKS[props.surface];
  return !isToggle(control) || control.active ? tone.strong : tone.light;
}

// Classes, in the template so the icon-scale guardrail sees them: a step
// takes the secondary rung and a toggle the smaller one (design-system.css §
// transport roles), and everything but the main button is `player-extra`,
// which the phone's mini-bar hides.

const REPEAT_LABEL_KEYS = {
  off: 'player.repeatOff',
  context: 'player.repeatContext',
  track: 'player.repeatTrack',
};

// A button that says what it does in words, by its command.
const LABEL_KEYS = {
  take_over: 'player.takeOver',
  stop: 'player.stop',
  resume_playback: 'player.play',
};

function toggleLabel(control) {
  if (control.id === 'repeat') return t(REPEAT_LABEL_KEYS[control.mode]);
  return t('player.shuffle');
}

// A labelled command spins until the source stops listing it (a take-over:
// until the session it brings arrives), until it is refused, or until it is
// given up on.
const PENDING_TIMEOUT_MS = 10000;
const timer = useTimer();
const pending = ref(null);
let pendingTimer = null;

function clearPending() {
  pending.value = null;
  if (pendingTimer) timer.clear(pendingTimer);
  pendingTimer = null;
}

watch(controls, (listed) => {
  if (pending.value && !listed.includes(pending.value)) clearPending();
});

async function press(control) {
  if (control.command === 'skip') {
    if (controls.value.includes('skip')) emit('skip', control.seconds);
    return;
  }
  if (!isLabelled(control)) {
    sendSourceCommand(control.command, control.params);
    return;
  }
  if (pending.value) return;
  pending.value = control.command;
  pendingTimer = timer.setTimeout(clearPending, PENDING_TIMEOUT_MS);
  if (!await sendSourceCommand(control.command, control.params)) clearPending();
}
</script>

<style scoped>
.player-transport {
  display: flex;
  align-items: center;
}

/* === PLATE (AudioPlayerFull) ===
   No ground: the row spans the column, edge to edge with the progress bar
   above it. With toggles, the steps and the main button touch, their tap
   targets alone spacing the glyphs, so prev/play/next read as one control;
   the toggles go to the ends, and the row reaches past the column by what
   their target holds around the glyph, so the glyphs land on the bar's ends.
   `--toggle-target` is that target, read by both. */
.player-transport--plate {
  --toggle-target: 56px;
  color: var(--color-text);
}

.player-transport--plate .player-transport--toggles {
  margin-inline: calc((var(--transport-toggle) - var(--toggle-target)) / 2);
}

/* Three buttons and nothing flanking them: they spread a little. */
.player-transport--plate .player-transport-row:not(.player-transport--toggles, .player-transport--labelled-end) {
  gap: var(--space-06);
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
.player-transport--plate .player-transport--toggles .player-button {
  width: 64px;
  height: 64px;
}

.player-transport--plate .player-transport--toggles .player-button--primary {
  width: 80px;
  height: 80px;
}

.player-transport--plate .player-button.player-button--toggle {
  flex-shrink: 0;
  width: var(--toggle-target);
  height: var(--toggle-target);
}

/* The row inside the swap, which holds the transport's layout: the steps
   centred, what flanks them pushed to the ends by an auto margin on the side
   facing the steps. */
.player-transport-row {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1;
  min-width: 0;
}

.player-transport-edge--start {
  margin-inline-end: auto;
}

.player-transport-edge--end {
  margin-inline-start: auto;
}

.transport-swap-enter-active {
  transition: opacity var(--transition-in-out), transform var(--transition-spring);
}

.transport-swap-leave-active {
  transition: opacity var(--transition-fast-leave), transform var(--transition-fast-leave);
}

.transport-swap-enter-from,
.transport-swap-leave-to {
  opacity: 0;
  transform: scale(0.96);
}

/* A labelled button takes the row's width at its own height, centred in the
   box of the main button it stands for — on the plate its 90px target, on the
   card its glyph and ghost padding — so the row keeps its height when the
   transport comes back. Beside the end slot it fills what the slot leaves. */
.player-transport-labelled {
  display: flex;
  align-items: center;
  flex: 1;
  min-width: 0;
}

.player-transport--plate .player-transport-labelled {
  height: 90px;
}

.player-transport--card .player-transport-labelled {
  height: calc(var(--transport-primary) + 2 * var(--space-02));
}

.player-transport-labelled .player-button--labelled {
  width: 100%;
}

.player-transport--labelled-end {
  gap: var(--space-03);
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
   The bar's own ghost buttons, at its compact scale: the trio centred, its
   ghost padding alone spacing it, and once a toggle is listed, shuffle and
   repeat pushed to the row's two ends. PlayerBody lets the row reach into the
   card's side padding by that ghost padding, so the toggles' glyphs land on
   the progress bar's ends. */
.player-transport--card {
  color: var(--color-text-on-contrast);
  width: 100%;
}

.player-transport--card .player-transport-row:not(.player-transport--toggles, .player-transport--labelled-end) {
  gap: var(--space-04);
}

/* Buttons with no ghost padding take it back, so their edges stay on the
   bar's. */
.player-transport--card .player-transport-labelled,
.player-transport--card .player-transport--labelled-end {
  padding-inline: var(--space-02);
}

.player-transport--card .player-transport--labelled-end .player-transport-labelled {
  height: auto;
  padding-inline: 0;
}

/* Beside the end slot the row holds the main button's box itself, and the
   labelled button and the slot's sit at its bottom, on the card's edge. */
.player-transport--card .player-transport--labelled-end {
  height: calc(var(--transport-primary) + 2 * var(--space-02));
  align-items: flex-end;
}

/* The spacer holds the end a missing toggle would take: a ghost button is its
   glyph plus the ghost padding on each side. */
.player-transport--card .player-button--toggle:not(.icon-button) {
  width: calc(var(--transport-toggle) + 2 * var(--space-02));
  height: calc(var(--transport-toggle) + 2 * var(--space-02));
  flex-shrink: 0;
}

@media (max-aspect-ratio: 4/3) {
  /* The phone's glyphs are a rung smaller, so the kiosk's targets would open
     the trio as wide as the toggles' margins (28 against 26px between glyphs,
     measured at 390px): the targets shrink with them, still above a finger. */
  .player-transport--plate .player-transport-row .player-button:not(.player-button--toggle) {
    width: 56px;
    height: 56px;
  }

  .player-transport--plate .player-transport-row .player-button.player-button--primary {
    width: 64px;
    height: 64px;
  }

  .player-transport--plate .player-transport-labelled {
    height: 64px;
  }

  .player-transport--plate {
    --toggle-target: 48px;
  }

  /* The mini-bar centres its one row. */
  .player-transport--card .player-transport--labelled-end {
    align-items: center;
  }
}
</style>
