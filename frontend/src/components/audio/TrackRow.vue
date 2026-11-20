<template>
  <div v-press.flat="!editing" class="track-row" :class="{ current, editing }" @click="onRowClick">
    <div class="track-index">
      <div class="track-position">
        <LoadingSpinner v-if="loading && !showCover" :size="20" />
        <div v-else-if="current && playing" ref="indicatorRef" class="playing-indicator" aria-hidden="true">
          <span class="bar"></span>
          <span class="bar"></span>
          <span class="bar"></span>
        </div>
        <span v-else class="track-number text-mono-large">{{ number }}</span>
      </div>
      <LazyImage v-if="showCover" :src="coverUrl" :fallback="musicPlaceholder"
        :alt="displayTitle" lazy :blurred="loading" class="track-cover">
        <transition name="loading-fade">
          <div v-if="loading" class="card-loading-overlay">
            <LoadingSpinner :size="20" />
          </div>
        </transition>
      </LazyImage>
    </div>

    <div class="track-main">
      <div class="track-title-row">
        <p class="track-title text-body-medium">{{ displayTitle }}</p>
        <span v-if="feat" class="track-feat text-mono-small">{{ t('musicLibrary.featuring', { artists: feat }) }}</span>
      </div>
      <!-- Text only: the whole row plays, and a page is reached from its menu. -->
      <p v-if="showArtist && song.artist" class="track-artist text-body-small">{{ song.artist }}</p>
    </div>

    <div v-if="editing" class="track-edit">
      <button v-press type="button" class="track-icon-btn track-remove"
        :aria-label="t('musicLibrary.playlists.remove')"
        @pointerdown.stop @click.stop="$emit('remove')">
        <SvgIcon name="minus" :size="20" />
      </button>
      <div class="track-grip" @pointerdown.stop.prevent="$emit('grip-down', $event)">
        <SvgIcon name="dragHandle" :size="24" />
      </div>
    </div>
    <template v-else>
      <span class="track-duration text-mono-medium">{{ formatDuration(song.duration) }}</span>
      <!-- A caller's own menu in place of the button. Its click reaches the
           document, so a menu left open on another row hears it and closes. -->
      <div v-if="$slots.menu" class="track-menu-slot" @pointerdown.stop>
        <slot name="menu" />
      </div>
      <button v-else-if="showMenu" v-press type="button" class="track-icon-btn track-menu hit-outset"
        :aria-label="t('musicLibrary.playlists.addToPlaylist')"
        @pointerdown.stop @click.stop="$emit('menu')">
        <SvgIcon name="threeDots" :size="24" />
      </button>
    </template>
  </div>
</template>

<script>
import { ref } from 'vue';

// The playing indicator's bounce — three bars, each 0.8 s there and back on
// ease-in-out, a quarter-cycle apart — stepped INDICATOR_HZ times a second
// rather than animated every frame, the way the Lyrics backdrop drifts. A CSS
// animation made Chromium compose a frame per vsync for as long as the row was
// on screen (a CD's tracklist: the whole disc), where the rest of the playing
// screen asks for about one a second. Each bar holds a paused animation the
// browser evaluates on its own curve; the tick only moves its clock, so the row
// never re-renders for it. Not measured on the Pi yet.
const INDICATOR_HZ = 10;
const BOUNCE_MS = 800;
const BAR_DELAYS_MS = [0, 150, 300];
const BOUNCE_KEYFRAMES = [
  { transform: 'scaleY(0.4)', easing: 'ease-in-out' },
  { transform: 'scaleY(1)', easing: 'ease-in-out' },
  { transform: 'scaleY(0.4)' },
];
const BOUNCE_TIMING = { duration: BOUNCE_MS, iterations: Infinity };

// How long a tapped row spins while the state shows nothing at all: a start that
// fails raises its own banner. Once the session is loading, the backend's own
// loading watchdog bounds the wait instead.
const START_SPIN_MAX_MS = 10000;

// The row last tapped to play, across every list: one spins at a time, so a
// second tap takes the spinner from the first.
const startingRow = ref(null);
</script>

<script setup>
import { computed, watch, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import LazyImage from '@/components/ui/LazyImage.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import { useTimer } from '@/composables/useTimer';
import { useDelayedFlag } from '@/composables/useDelayedFlag';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { musicPlaceholder } from '@/constants/placeholders';

const props = defineProps({
  // The catalogue record: { title | name, artist, duration }. `duration` is in
  // SECONDS — Subsonic's unit, and the opposite of ProgressBar's milliseconds
  // next door in this directory.
  song: {
    type: Object,
    required: true,
  },
  number: {
    type: [Number, String],
    required: true,
  },
  current: {
    type: Boolean,
    default: false,
  },
  playing: {
    type: Boolean,
    default: false,
  },
  showArtist: {
    type: Boolean,
    default: false,
  },
  feat: {
    type: String,
    default: '',
  },
  showMenu: {
    type: Boolean,
    default: false,
  },
  editing: {
    type: Boolean,
    default: false,
  },
  fallbackTitle: {
    type: String,
    default: '',
  },
  showCover: {
    type: Boolean,
    default: false,
  },
  coverUrl: {
    type: String,
    default: '',
  },
});

const emit = defineEmits(['play', 'menu', 'remove', 'grip-down']);

const { t } = useI18n();
const timer = useTimer();
// Its own timer: the start spinner's clearAll() must not stop the bounce.
const indicatorTimer = useTimer();
const audioStore = useUnifiedAudioStore();

const indicatorRef = ref(null);
let bounce = null;

function stopBounce() {
  if (!bounce) return;
  indicatorTimer.clear(bounce.interval);
  bounce.bars.forEach((bar) => bar.cancel());
  bounce = null;
}

// Started when the indicator is drawn, canceled when it goes (or the row does).
watch(indicatorRef, (el) => {
  stopBounce();
  // No Web Animations (a test DOM): the bars stand still, which is all they mean.
  if (!el || typeof el.animate !== 'function') return;
  const start = performance.now();
  const bars = [...el.children].map((bar) => {
    const animation = bar.animate(BOUNCE_KEYFRAMES, BOUNCE_TIMING);
    animation.pause();
    return animation;
  });
  // A lagging bar is one whose clock runs behind; a cycle ahead keeps it positive.
  const tick = () => {
    const elapsed = performance.now() - start + BOUNCE_MS;
    bars.forEach((bar, i) => { bar.currentTime = elapsed - BAR_DELAYS_MS[i]; });
  };
  tick();
  bounce = { bars, interval: indicatorTimer.setInterval(tick, 1000 / INDICATOR_HZ) };
});
onUnmounted(stopBounce);

const displayTitle = computed(() => props.song.title || props.song.name || props.fallbackTitle);

// A tapped row spins until it plays, or until the session settles on anything
// else (another row, the header's play, a track the listing names otherwise).
// One token per row; the module's `startingRow` holds the last one tapped.
const token = {};
const starting = computed(() => startingRow.value === token && !(props.current && props.playing));
// Drawn only once the start is long enough to notice, as every wait is.
const loading = useDelayedFlag(starting);

function stopStarting() {
  if (startingRow.value === token) startingRow.value = null;
}

watch(starting, (now, before) => {
  if (before && !now) stopStarting();
});

const sessionMark = computed(() => {
  const session = audioStore.systemState.session;
  return session ? `${session.id}|${session.phase}|${session.title}` : '';
});

// No session is the gap of a source switch, which settles nothing.
watch(sessionMark, () => {
  const session = audioStore.systemState.session;
  if (!starting.value || !session) return;
  if (session.phase === 'loading') timer.clearAll();
  else stopStarting();
});

function onRowClick(event) {
  if (event.target.closest('.track-menu-slot')) return;
  if (props.editing) return;
  if (!(props.current && props.playing)) {
    startingRow.value = token;
    timer.clearAll();
    timer.setTimeout(stopStarting, START_SPIN_MAX_MS);
  }
  emit('play', props.number);
}

function formatDuration(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) {
    return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
  }
  return `${m}:${String(sec).padStart(2, '0')}`;
}
</script>

<style scoped>
.track-row {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-04) 0;
  border-bottom: 1px solid var(--color-border);
  cursor: pointer;
  min-width: 0;
  transition: var(--transition-press);
}

.track-row.editing {
  cursor: default;
}

.track-index {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: var(--space-05);
}

.track-position {
  width: 28px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

.track-cover {
  width: 40px;
  height: 40px;
  border-radius: var(--radius-02);
  background: var(--color-fill-faint);
}

.track-number {
  color: var(--color-text-secondary);
}

.track-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  overflow: hidden;
}

.track-title-row {
  display: flex;
  align-items: baseline;
  gap: var(--space-02);
  min-width: 0;
}

.track-title {
  margin: 0;
  min-width: 0;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.track-feat {
  flex-shrink: 0;
  color: var(--color-text-secondary);
  white-space: nowrap;
}

.track-artist {
  margin: 0;
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.track-duration {
  flex-shrink: 0;
  color: var(--color-text-secondary);
}

.track-row.current .track-number,
.track-row.current .track-title,
.track-row.current .track-duration,
.track-row.current .track-artist,
.track-row.current .track-feat {
  color: var(--color-brand);
}

.track-icon-btn {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  border-radius: var(--radius-02);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: color var(--transition-fast), var(--transition-press);
}

.track-remove {
  color: var(--color-error);
}

.track-menu-slot {
  flex-shrink: 0;
  display: flex;
}

.track-edit {
  flex-shrink: 0;
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: var(--space-01);
}

.track-grip {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  color: var(--color-text-tertiary);
  cursor: grab;
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
}

.playing-indicator {
  display: flex;
  align-items: flex-end;
  justify-content: center;
  gap: 2px;
  height: 14px;
}

.playing-indicator .bar {
  display: block;
  width: 3px;
  background: var(--color-brand);
  border-radius: 1px;
}

/* Heights only: the bounce is stepped from the script (INDICATOR_HZ). */
.playing-indicator .bar:nth-child(1) {
  height: 60%;
}

.playing-indicator .bar:nth-child(2) {
  height: 100%;
}

.playing-indicator .bar:nth-child(3) {
  height: 40%;
}
</style>
