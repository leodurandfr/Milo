<template>
  <div class="tracklist-header">
    <LazyImage
      :src="store.coverUrl(coverId, 600)"
      :fallback="albumPlaceholder"
      :alt="title"
      priority="high"
      class="tracklist-cover"
    />

    <div class="tracklist-meta">
      <div class="tracklist-titles">
        <h2 class="tracklist-title heading-1">{{ title }}</h2>
        <p v-if="subtitle || subtitleMeta" class="tracklist-subtitle">
          <span v-if="subtitle" class="text-body">{{ subtitle }}</span>
          <span v-if="subtitle && subtitleMeta" class="text-mono"> · </span>
          <span v-if="subtitleMeta" class="text-mono">{{ subtitleMeta }}</span>
        </p>
      </div>

      <div class="tracklist-actions">
        <Button variant="brand" left-icon="play" @click="$emit('play')">
          {{ t('musicLibrary.play') }}
        </Button>
        <Button v-if="showShuffle" variant="on-dark" @click="$emit('shuffle')">
          {{ t('musicLibrary.shuffle') }}
        </Button>
        <IconButton
          v-if="showFavorite"
          :icon="isFavorite ? 'heart' : 'heartOff'"
          variant="on-dark"
          @click="$emit('toggle-favorite')"
        />
        <!-- Extra actions (e.g. the playlist Edit/Done toggle). -->
        <slot name="actions"></slot>
      </div>
    </div>
  </div>
</template>

<script setup>
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import LazyImage from '@/components/ui/LazyImage.vue';
import albumPlaceholder from '@/assets/images/album-placeholder.svg';
import Button from '@/components/ui/Button.vue';
import IconButton from '@/components/ui/IconButton.vue';

defineProps({
  coverId: {
    type: String,
    default: '',
  },
  title: {
    type: String,
    required: true,
  },
  subtitle: {
    type: String,
    default: '',
  },
  subtitleMeta: {
    type: String,
    default: '',
  },
  showShuffle: {
    type: Boolean,
    default: true,
  },
  showFavorite: {
    type: Boolean,
    default: false,
  },
  isFavorite: {
    type: Boolean,
    default: false,
  },
});

defineEmits(['play', 'shuffle', 'toggle-favorite']);

const { t } = useI18n();
const store = useMusicLibraryStore();
</script>

<style scoped>
.tracklist-header {
  display: flex;
  flex-direction: row;
  align-items: flex-end;
  gap: var(--space-03);
  background: var(--color-background-contrast);
  border-radius: var(--radius-04);
  padding: var(--space-03) var(--space-04) var(--space-03) var(--space-03);
}

.tracklist-cover {
  flex-shrink: 0;
  width: 128px;
  height: 128px;
  border-radius: var(--radius-02);
}

.tracklist-meta {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.tracklist-titles {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  min-width: 0;
}

.tracklist-title {
  margin: 0;
  color: var(--color-text-contrast);
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.tracklist-subtitle {
  margin: 0;
  color: var(--color-text-contrast-50);
}

.tracklist-subtitle .text-body {
  color: var(--color-text-light);
}

.tracklist-actions {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: var(--space-02);
  flex-wrap: wrap;
}

@media (max-aspect-ratio: 4/3) {
  .tracklist-cover {
    width: 96px;
    height: 96px;
  }
}
</style>
