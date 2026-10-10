<template>
  <div v-press.flat="!editing" class="track-row" :class="{ current, editing }" @click="onRowClick">
    <div class="track-index">
      <div class="track-position">
        <LoadingSpinner v-if="loading && !showCover" :size="20" />
        <div v-else-if="current && playing" class="playing-indicator" aria-hidden="true">
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
        <SvgIcon name="threeDots" :size="20" />
      </button>
    </template>
  </div>
</template>

<script>
import { ref } from 'vue';

// How long a tapped row spins while the state shows nothing at all: a start that
// fails raises its own banner. Once the session is loading, the backend's own
// loading watchdog bounds the wait instead.
const START_SPIN_MAX_MS = 10000;

// The row last tapped to play, across every list: one spins at a time, so a
// second tap takes the spinner from the first.
const startingRow = ref(null);
</script>

<script setup>
import { computed, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import LazyImage from '@/components/ui/LazyImage.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import { useTimer } from '@/composables/useTimer';
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
  // A page this row leads to is opening (from its menu): the row spins until
  // the page has something to draw.
  opening: {
    type: Boolean,
    default: false,
  },
});

const emit = defineEmits(['play', 'menu', 'remove', 'grip-down']);

const { t } = useI18n();
const timer = useTimer();
const audioStore = useUnifiedAudioStore();

const displayTitle = computed(() => props.song.title || props.song.name || props.fallbackTitle);

// A tapped row spins until it plays, or until the session settles on anything
// else (another row, the header's play, a track the listing names otherwise).
// One token per row; the module's `startingRow` holds the last one tapped.
const token = {};
const starting = computed(() => startingRow.value === token && !(props.current && props.playing));
const loading = computed(() => props.opening || starting.value);

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
  gap: var(--space-03);
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
  animation: bar-bounce 0.8s ease-in-out infinite;
}

.playing-indicator .bar:nth-child(1) {
  height: 60%;
  animation-delay: 0s;
}

.playing-indicator .bar:nth-child(2) {
  height: 100%;
  animation-delay: 0.15s;
}

.playing-indicator .bar:nth-child(3) {
  height: 40%;
  animation-delay: 0.3s;
}

@keyframes bar-bounce {
  0%, 100% { transform: scaleY(0.4); }
  50% { transform: scaleY(1); }
}
</style>
