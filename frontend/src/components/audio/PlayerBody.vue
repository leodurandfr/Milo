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

     The title opens the album and the artist line the artist, where the
     navigation around the player says there is one to open (PLAYER_NAVIGATION)
     — emitted, never followed. -->
<template>
  <div class="player-body" :class="`player-body--${surface}`">
    <div class="player-body-info" :class="{ 'no-controls': !hasTransport }">
      <!-- A shell can draw this block itself: the phone's swipe carousel. -->
      <slot name="info">
        <!-- Where the music comes from, at the top of the block: always on the
             full player, centred; on the card, ranged left, only where nothing
             else on it says so (usePlayerMetadata's linesOf). Never on the
             phone's mini-bar, which has room for one line each of title and
             secondary. -->
        <!-- On the card it comes and goes with what plays (a detected song,
             another device), so it opens and closes rather than popping: the
             lines below move with it. The leaving one keeps its last label. -->
        <Transition name="source-reveal">
          <div v-if="surface === 'full' || sourceOnCard" class="body-source-slot">
            <div class="body-source-row">
              <SourceBar class="body-source" :source="source" :size="surface === 'card' ? 'small' : 'medium'"
                :label="sourceLabel" :image="sourceImage" />
            </div>
          </div>
        </Transition>
        <!-- The title and its line, centred in what the source bar leaves. -->
        <div class="body-lines">
          <template v-if="surface === 'full'">
            <h1 v-press="albumLink" class="body-title heading-1" :class="{ 'is-link': albumLink }"
              @click="onTitleClick">{{ title }}</h1>
            <p v-if="secondaryLine" v-press="artistLink" class="body-secondary heading-2"
              :class="{ 'is-link': artistLink }" @click="onSecondaryClick">{{ secondaryLine }}</p>
          </template>
          <PlayerInfoText v-else class="card-lines"
            :class="{ 'has-album-link': albumLink, 'has-artist-link': artistLink }"
            :title="title" :secondary="secondaryLine || null" @click="onCardLinesClick" />
        </div>
        <!-- The phone's mini-bar: one line each, as the swipe carousel's cells. -->
        <PlayerInfoText v-if="surface === 'card'" variant="line" class="body-mini-lines"
          :title="title" :secondary="secondaryLine || null" />
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
          :interactive="canSeek" :loading="phase === 'loading'" :variant="surface === 'card' ? 'on-contrast' : 'default'"
          :animateIn="surface === 'full'" @seek="seekTo" />
      </div>
      <div v-if="hasTransport" class="body-transport" :class="{ 'transport-scale--compact': surface === 'card' }">
        <PlayerTransport :source="source" :surface="surface === 'card' ? 'card' : 'plate'" @skip="skip">
          <template v-if="$slots['transport-end']" #end="slotProps">
            <slot name="transport-end" v-bind="slotProps" />
          </template>
        </PlayerTransport>
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

const emit = defineEmits(['title-click', 'secondary-click']);

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

// The album behind the title and the artist behind the line, where the
// navigation says there is one. Not on the phone's mini-bar, which is one
// target as a whole: the full player.
const navigation = inject(PLAYER_NAVIGATION, null);
const linksShown = computed(() => !(props.surface === 'card' && isMobile.value));
const albumLink = computed(() => !!navigation?.canOpenAlbum.value && linksShown.value);
const artistLink = computed(() => !!navigation?.canOpenArtist.value && linksShown.value);

function onTitleClick() {
  if (albumLink.value) emit('title-click');
}

function onSecondaryClick() {
  if (artistLink.value) emit('secondary-click');
}

// PlayerInfoText draws the card's two lines; caught by their class.
function onCardLinesClick(event) {
  if (event.target.closest('.player-info-title')) onTitleClick();
  else if (event.target.closest('.player-info-secondary')) onSecondaryClick();
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

/* The source bar on top, at the block's padding; the title and its line
   centred in the rest, never closer to the bar than the block's gap. */
.player-body--full .player-body-info {
  --body-info-gap: var(--space-06);
  gap: var(--body-info-gap);
}

.body-lines {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
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
  color: var(--color-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.body-title.is-link,
.body-secondary.is-link {
  cursor: pointer;
}

/* A link is as wide as its text, so a tap beside a short title does not open
   the album; max-width keeps the ellipsis on a long one. */
.player-body--full .body-title,
.player-body--full .body-secondary {
  align-self: center;
  max-width: 100%;
}

/* === CARD (AudioPlayer): lines ranged left on the dark card === */
.player-body--card {
  flex: 1;
  gap: var(--space-04);
}

.player-body--card .player-body-info {
  --body-info-gap: var(--space-04);
  flex: 1;
  gap: var(--body-info-gap);
  padding: 0 var(--space-04);
}

/* The source bar's slot opens from nothing: its row from 0 to its height,
   and the block's gap with it, so the lines glide instead of jumping. The
   bar itself keeps its own height and overflows the row while it opens —
   nothing of it is squeezed or cut — and comes in whole, icon and label
   together, by a short drop and a fade. */
.body-source-slot {
  display: grid;
  grid-template-rows: 1fr;
}

/* The row's box follows the track down to 0; the bar inside keeps its height
   and overflows it, visible. */
.body-source-row {
  min-height: 0;
}

/* In and out on the same springs, as the player's other moves: the bar's
   drop on the full one, the room it makes on the light one so the lines
   settle without a bounce. An ease-in on the way out read as the entrance
   played backwards. */
.source-reveal-enter-active,
.source-reveal-leave-active {
  transition:
    grid-template-rows var(--transition-spring-light),
    margin-bottom var(--transition-spring-light);
}

.source-reveal-enter-active .body-source,
.source-reveal-leave-active .body-source {
  transition: transform var(--transition-spring), opacity var(--transition-in-out);
}

.source-reveal-enter-from,
.source-reveal-leave-to {
  grid-template-rows: 0fr;
  margin-bottom: calc(-1 * var(--body-info-gap));
}

.source-reveal-enter-from .body-source,
.source-reveal-leave-to .body-source {
  opacity: 0;
  transform: translateY(calc(-1 * var(--space-03)));
}

.player-body--card .player-body-bottom {
  gap: var(--space-04);
  padding: 0 var(--space-04);
}

.card-lines.has-album-link :deep(.player-info-title),
.card-lines.has-artist-link :deep(.player-info-secondary) {
  cursor: pointer;
}

/* On the card the bar is ranged left, and the title keeps to two lines: the
   card's height is shared with the cover above it. */
.player-body--card .body-source {
  justify-content: flex-start;
}

.player-body--card .card-lines :deep(.player-info-title) {
  -webkit-line-clamp: 2;
}

/* Its label on the dark card, in the card's ink. */
.player-body--card .body-source :deep(.source-bar-label) {
  color: var(--color-text-on-contrast);
}

/* The mini-bar's one-line pair, hidden on the kiosk's card. */
.body-mini-lines {
  display: none;
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
    gap: 0;
  }

  .player-body--card .body-lines,
  .player-body--card .body-source-slot {
    display: none;
  }

  /* Cut by a right-edge fade rather than an ellipsis. */
  .player-body--card .body-mini-lines {
    display: flex;
    min-width: 0;
    -webkit-mask-image: linear-gradient(to right, black calc(100% - var(--space-05)), transparent 100%);
    mask-image: linear-gradient(to right, black calc(100% - var(--space-05)), transparent 100%);
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
