<template>
  <div v-press="!editing" class="track-row" :class="{ current, editing }" @click="onRowClick">
    <div class="track-index">
      <div class="track-position">
        <div v-if="current && playing" class="playing-indicator" aria-hidden="true">
          <span class="bar"></span>
          <span class="bar"></span>
          <span class="bar"></span>
        </div>
        <span v-else class="track-number text-mono-large">{{ number }}</span>
      </div>
      <LazyImage v-if="showCover" :src="coverUrl" :fallback="musicPlaceholder"
        :alt="displayTitle" lazy class="track-cover" />
    </div>

    <div class="track-main">
      <div class="track-title-row">
        <p class="track-title text-body">{{ displayTitle }}</p>
        <span v-if="feat" class="track-feat text-mono-small">{{ t('musicLibrary.featuring', { artists: feat }) }}</span>
      </div>
      <p v-if="showArtist && song.artist" class="track-artist text-mono-medium">{{ song.artist }}</p>
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
      <button v-if="showMenu" v-press type="button" class="track-icon-btn track-menu"
        :aria-label="t('musicLibrary.playlists.addToPlaylist')"
        @pointerdown.stop @click.stop="$emit('menu')">
        <SvgIcon name="threeDots" :size="20" />
      </button>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import LazyImage from '@/components/ui/LazyImage.vue';
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

const displayTitle = computed(() => props.song.title || props.song.name || props.fallbackTitle);

function onRowClick() {
  if (!props.editing) emit('play', props.number);
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

.track-row:last-child {
  border-bottom: none;
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
  background: var(--color-background-neutral-12);
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
