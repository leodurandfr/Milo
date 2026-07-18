<template>
  <div v-press="!editing" class="track-row" :class="{ current, editing }" @click="onRowClick">
    <div class="track-index">
      <!-- Animated bars while this row is the one playing; otherwise the number. -->
      <div v-if="current && playing" class="playing-bars" aria-hidden="true">
        <span></span><span></span><span></span>
      </div>
      <span v-else class="track-number text-mono">{{ number }}</span>
    </div>

    <div class="track-main">
      <p class="track-title heading-4">{{ song.title || song.name }}</p>
      <p v-if="showArtist && song.artist" class="track-artist text-mono">{{ song.artist }}</p>
    </div>

    <!-- Edit mode: remove + drag grip. Otherwise: duration + optional ⋯ menu.
         @pointerdown.stop keeps the tap from bubbling to the row's v-press, which
         would otherwise capture the pointer and steal the resulting click. -->
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
      <span class="track-duration text-mono">{{ formatDuration(song.duration) }}</span>
      <button v-if="showMenu" v-press type="button" class="track-icon-btn track-menu"
        :aria-label="t('musicLibrary.playlists.addToPlaylist')"
        @pointerdown.stop @click.stop="$emit('menu')">
        <SvgIcon name="threeDots" :size="20" />
      </button>
    </template>
  </div>
</template>

<script setup>
import { useI18n } from '@/services/i18n';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import { formatDuration } from '../format.js';

const props = defineProps({
  song: {
    type: Object,
    required: true,
  },
  // Display position (1-based) shown in the index column.
  number: {
    type: [Number, String],
    required: true,
  },
  // This row is the active queue entry.
  current: {
    type: Boolean,
    default: false,
  },
  // Playback is actively running (vs. paused) for the current row.
  playing: {
    type: Boolean,
    default: false,
  },
  // Show the per-track artist line (mixed contexts: genre/playlist/search/queue).
  showArtist: {
    type: Boolean,
    default: false,
  },
  // Show the ⋯ overflow (add-to-playlist). Suppressed in edit mode.
  showMenu: {
    type: Boolean,
    default: false,
  },
  // Playlist edit mode: swap play-on-tap for a remove button + drag grip.
  editing: {
    type: Boolean,
    default: false,
  },
});

const emit = defineEmits(['play', 'menu', 'remove', 'grip-down']);

const { t } = useI18n();

function onRowClick() {
  if (!props.editing) emit('play');
}
</script>

<style scoped>
.track-row {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-02) var(--space-03);
  border-radius: var(--radius-03);
  cursor: pointer;
  min-width: 0;
  transition: background var(--transition-fast);
}

.track-row.editing {
  cursor: default;
}

.track-row.current {
  background: var(--color-background-neutral-50);
}

.track-index {
  flex-shrink: 0;
  width: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.track-number {
  color: var(--color-text-secondary);
}

.track-row.current .track-number {
  color: var(--color-brand);
}

.track-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  overflow: hidden;
}

.track-title {
  margin: 0;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.track-row.current .track-title {
  color: var(--color-brand);
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

/* Inline icon buttons (⋯ menu, remove) — ghost style, no filled surface. */
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

/* Edit mode: remove + drag grip side by side. */
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
  color: var(--color-text-light);
  cursor: grab;
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
}

/* Now-playing equalizer bars */
.playing-bars {
  display: flex;
  align-items: flex-end;
  gap: 2px;
  height: 16px;
}

.playing-bars span {
  width: 3px;
  background: var(--color-brand);
  border-radius: var(--radius-01);
  animation: eq-bar 900ms ease-in-out infinite;
}

.playing-bars span:nth-child(1) { animation-delay: 0ms; }
.playing-bars span:nth-child(2) { animation-delay: 220ms; }
.playing-bars span:nth-child(3) { animation-delay: 440ms; }

@keyframes eq-bar {
  0%, 100% { height: 5px; }
  50% { height: 16px; }
}
</style>
