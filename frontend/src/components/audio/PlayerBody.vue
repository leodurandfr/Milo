<!-- PlayerBody.vue - What both players draw under or beside the cover: the
     title and its lines, the progress bar, the transport. One implementation,
     read from the state (the player's one reading, usePlayerState, which the
     shell makes; useSourceProgress), drawn by the two shells: AudioPlayerFull, the full
     player, centred (`surface="full"`); AudioPlayer, the playing bar of the
     navigation, ranged left on its dark card (`surface="card"`), which on the
     phone's mini-bar folds into one row. The shells keep everything else — the
     cover, the full player's top row and backdrop, the bar's gestures.

     Above the title, in both: the SourceBar, where the music comes from (the
     source, or the station, the show, the sender, the account).

     The artist line opens the artist where the navigation around the player
     says there is one to open (PLAYER_NAVIGATION) — emitted, never followed. -->
<template>
  <div class="player-body" :class="`player-body--${surface}`">
    <div class="player-body-info" :class="{ 'no-controls': !hasTransport }">
      <!-- A shell can draw this block itself: the phone's swipe carousel. -->
      <slot name="info">
        <!-- The title and its line, centred in the block whether or not the
             source bar rides above them: the bar hangs off the group's top edge
             rather than sitting in the flow, so it never pushes them down. -->
        <div class="body-lines">
          <!-- Where the music comes from: always on the full player, centred;
               on the card, ranged left, only where nothing else on it says so
               (usePlayerMetadata's linesOf). Never on the phone's mini-bar,
               which has room for one line each of title and secondary. -->
          <SourceBar v-if="surface === 'full' || sourceOnCard" class="body-source" :source="source"
            :label="sourceLabel" :image="sourceImage" />
          <template v-if="surface === 'full'">
            <h1 class="body-title heading-1">{{ title }}</h1>
            <p v-if="secondaryLine" v-press="artistLink" class="body-secondary heading-2"
              :class="{ 'is-link': artistLink }" @click="onSecondaryClick">{{ secondaryLine }}</p>
          </template>
          <PlayerInfoText v-else class="card-lines" :class="{ 'has-link': artistLink }"
            :title="title" :secondary="secondaryLine || null" @click="onCardLinesClick" />
        </div>
        <template v-if="surface === 'card'">
          <!-- The phone's mini-bar: one line each. -->
          <p class="body-line body-line--title text-body">{{ title }}</p>
          <p v-if="secondaryLine" class="body-line body-line--secondary text-body">{{ secondaryLine }}</p>
        </template>
      </slot>
    </div>

    <div class="player-body-bottom">
      <!-- With a transport the full player holds the bar's row, so the centred
           lines do not shift when the bar mounts on play; a receiver without
           one only takes it while it has a bar. The bar hides itself until the
           source has a duration and a position. -->
      <div v-if="surface === 'card' || hasTransport || hasProgress" class="body-progress" @click.stop>
        <ProgressBar :currentPosition="currentPosition" :duration="duration"
          :progressPercentage="progressPercentage" :isReady="isPositionInitialized"
          :interactive="canSeek" :loading="phase === 'loading'" :variant="surface === 'card' ? 'dark' : 'light'"
          :animateIn="surface === 'full'" @seek="seekTo" />
      </div>
      <div v-if="hasTransport" class="body-transport" :class="{ 'transport-scale--compact': surface === 'card' }">
        <PlayerTransport :source="source" :surface="surface === 'card' ? 'card' : 'plate'" @skip="skip" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, inject } from 'vue';
import { useIsMobile } from '@/composables/useIsMobile';
import { usePlayerState } from '@/composables/usePlayerState';
import { useSourceProgress } from '@/composables/useSourceProgress';
import { PLAYER_NAVIGATION } from '@/composables/usePlayerExpansion';
import { swipeMove } from '@/utils/playerControls';
import ProgressBar from './ProgressBar.vue';
import PlayerInfoText from './PlayerInfoText.vue';
import PlayerTransport from './PlayerTransport.vue';
import SourceBar from './SourceBar.vue';

const props = defineProps({
  source: {
    type: String,
    required: true
  },
  // Which shell draws it: the full player (centred, light ground) or the
  // playing bar's card (ranged left, dark ground).
  surface: {
    type: String,
    default: 'full',
    validator: (value) => ['full', 'card'].includes(value)
  }
});

const emit = defineEmits(['secondary-click']);

const { isMobile } = useIsMobile();
// The player's one reading of its state, made by the shell drawing this body.
const { metadata, controls: sourceControls } = usePlayerState(props.source);
const { title, secondaryLine, sourceLabel, sourceImage, sourceOnCard } = metadata;
const { controls, phase, liveControls, shownControls, sendSourceCommand } = sourceControls;
const {
  currentPosition, duration, progressPercentage, seekTo, skip, isPositionInitialized
} = useSourceProgress(props.source);

// The receivers (AirPlay, Qobuz) list no main command and draw no transport.
const hasTransport = computed(() => shownControls.value.some(control => control.row === 'transport'));
const canSeek = computed(() => liveControls.value.some(control => control.id === 'seek'));
const hasProgress = computed(() => duration.value > 0 && isPositionInitialized.value);

// The artist behind the line, where the navigation says there is one. Not on
// the phone's mini-bar, which is one target as a whole: the full player.
const navigation = inject(PLAYER_NAVIGATION, null);
const artistLink = computed(() =>
  !!navigation?.canOpenArtist.value && !(props.surface === 'card' && isMobile.value)
);

function onSecondaryClick() {
  if (artistLink.value) emit('secondary-click');
}

// PlayerInfoText draws the card's secondary line; caught by its class.
function onCardLinesClick(event) {
  if (event.target.closest('.player-info-secondary')) onSecondaryClick();
}

// The phone's swipe on the mini-bar, which the shell detects and this body
// sends (utils/playerControls' swipeMove: a step, else the relative skip). A
// skip goes through this body's playhead, so a burst shows its sum.
function swipe(direction, shownIndex) {
  const move = swipeMove(controls.value, direction, shownIndex);
  if (!move) return;
  if (move.skip) skip(move.skip);
  else sendSourceCommand(move.command, move.params);
}

defineExpose({ swipe });
</script>

<style scoped>
.player-body {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.player-body-info {
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-width: 0;
}

.player-body-bottom {
  display: flex;
  flex-direction: column;
}

/* === FULL (AudioPlayerFull): centred lines, the bar, the plate === */
.player-body--full {
  flex: 1;
  justify-content: space-between;
}

.player-body--full .player-body-info {
  flex: 1;
  text-align: center;
  padding-top: var(--space-06);
}

/* The title and its line, the group the block centres. The source bar is
   positioned against it (below), out of the flow. */
.body-lines {
  position: relative;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.player-body--full .body-lines {
  gap: var(--space-03);
}

.player-body--full .player-body-info.no-controls {
  padding-top: 0;
}

.player-body--full .player-body-bottom {
  gap: var(--space-05);
}

/* Stagger on mount (first load, and back from CD's tracklist). */
.player-body--full > .player-body-info,
.player-body--full > .player-body-bottom {
  opacity: 0;
  transform: translateY(var(--space-05));
  animation:
    body-stagger-transform var(--transition-spring) forwards,
    body-stagger-opacity 0.4s ease forwards;
}

.player-body--full > .player-body-bottom { animation-delay: 100ms; }

@keyframes body-stagger-transform {
  to {
    transform: none;
  }
}

@keyframes body-stagger-opacity {
  to {
    opacity: 1;
  }
}

/* Reserve the progress bar's row height even while it's hidden (idle CD) so the
   centred lines do not shift when the bar mounts on play. Matches the bar's
   flex-row height, which the .time line-height (--line-height-mono-medium)
   dominates over the 8px track. */
.player-body--full .body-progress {
  min-height: var(--line-height-mono-medium);
}

.body-title {
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.body-secondary {
  color: var(--color-text-light);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.body-secondary.is-link {
  cursor: pointer;
}

/* === CARD (AudioPlayer): lines ranged left on the dark card === */
.player-body--card {
  flex: 1;
  gap: var(--space-04);
}

.player-body--card .player-body-info {
  flex: 1;
  padding: 0 var(--space-04);
}

.player-body--card .player-body-bottom {
  gap: var(--space-04);
  padding: 0 var(--space-04);
}

.card-lines.has-link :deep(.player-info-secondary) {
  cursor: pointer;
}

/* The source bar hangs above the title, --space-06 clear of it — 32px on the
   kiosk, 24px below 4:3, the token's own phone step — out of the flow, so the
   title and its line stay centred in the block whether it is there or not, and
   take no height from the card. It grows upward: a title on more lines moves
   the group's top, and the bar with it, never onto the title. */
.body-source {
  position: absolute;
  bottom: 100%;
  left: 0;
  right: 0;
  margin-bottom: var(--space-06);
}

/* On the card the bar hangs closer, --space-04 (16px), and the title keeps to
   two lines: the gap above the block is all there is between it and the cover,
   and at the kiosk's 115% interface scale a two-line title with a 32px gap put
   the bar 6px onto the cover (measured). */
.player-body--card .body-source {
  justify-content: flex-start;
  margin-bottom: var(--space-04);
}

.player-body--card .card-lines :deep(.player-info-title) {
  -webkit-line-clamp: 2;
}

/* Its label on the dark card, in the card's ink. */
.player-body--card .body-source :deep(.source-bar-label) {
  color: var(--color-text-contrast);
}

/* The mini-bar's one-line pair, hidden on the kiosk's card. */
.body-line {
  display: none;
  margin: 0;
}

/* The card centres its row; the full player's plate spans the column, edge to
   edge with the bar above it. */
.body-transport {
  display: flex;
  justify-content: center;
  align-items: center;
}

.player-body--full .body-transport {
  display: block;
}

@media (max-aspect-ratio: 4/3) {
  .player-body--full .player-body-info {
    padding: var(--space-06) 0 var(--space-03) 0;
  }

  .player-body--full .player-body-info.no-controls {
    padding: 0;
  }

  .player-body--full .player-body-bottom {
    margin-bottom: calc(env(safe-area-inset-bottom, 0px));
  }

  /* The mini-bar: one row — the two lines, then the main button. The body
     stays unpositioned so the progress strip below sits on the bar itself
     (its nearest positioned ancestor) and spans it edge to edge. */
  .player-body--card {
    flex-direction: row;
    align-items: center;
    gap: var(--space-03);
    min-width: 0;
  }

  .player-body--card .player-body-info {
    padding: 0;
    gap: var(--space-01);
  }

  .player-body--card .body-lines {
    display: none;
  }

  /* Every line exactly one line, cut by a right-edge fade rather than an
     ellipsis. */
  .player-body--card .body-line {
    display: block;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: clip;
    -webkit-mask-image: linear-gradient(to right, black calc(100% - var(--space-05)), transparent 100%);
    mask-image: linear-gradient(to right, black calc(100% - var(--space-05)), transparent 100%);
  }

  .body-line--title {
    color: var(--color-text-contrast);
  }

  .body-line--secondary {
    color: var(--color-text-contrast-50);
  }

  .player-body--card .player-body-bottom {
    flex-shrink: 0;
    padding: 0;
    gap: 0;
  }

  /* The progress: a thin strip pinned to the very bottom of the bar, clipped
     by the bar's own corners; no times. */
  .player-body--card .body-progress :deep(.progress-bar) {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    height: 2px;
    padding: 0;
    gap: 0;
  }

  .player-body--card .body-progress :deep(.progress-bar .time) {
    display: none;
  }

  .player-body--card .body-progress :deep(.progress-container),
  .player-body--card .body-progress :deep(.progress) {
    height: 100%;
    border-radius: 0;
  }

  /* The mini-bar's main button is sized against the 48px thumbnail beside it,
     not against the transport scale: the one place a tier token is bent rather
     than picked. */
  .player-body--card .body-transport {
    --transport-primary: 28px;
  }
}
</style>
