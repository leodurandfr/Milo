<template>
  <div class="tracklist-header">
    <LazyImage
      :src="store.coverUrl(coverId, 600)"
      :fallback-name="title"
      :alt="title"
      priority="high"
      class="tracklist-cover"
    />

    <div class="tracklist-meta">
      <div class="tracklist-titles">
        <h2 class="tracklist-title heading-1">{{ title }}</h2>
        <p v-if="subtitle" class="tracklist-subtitle text-mono">{{ subtitle }}</p>
      </div>

      <div class="tracklist-actions">
        <Button variant="brand" left-icon="play" @click="$emit('play')">
          {{ t('musicLibrary.play') }}
        </Button>
        <Button variant="background-strong" @click="$emit('shuffle')">
          {{ t('musicLibrary.shuffle') }}
        </Button>
        <IconButton
          v-if="showFavorite"
          :icon="isFavorite ? 'heart' : 'heartOff'"
          variant="background-strong"
          @click="$emit('toggle-favorite')"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import LazyImage from '@/components/ui/LazyImage.vue';
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
  gap: var(--space-05);
  align-items: flex-end;
}

.tracklist-cover {
  flex-shrink: 0;
  width: 180px;
  height: 180px;
  border-radius: var(--radius-05);
  background: var(--color-background-neutral-50);
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
  color: var(--color-text);
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.tracklist-subtitle {
  margin: 0;
  color: var(--color-text-secondary);
}

.tracklist-actions {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: var(--space-02);
  flex-wrap: wrap;
}

@media (max-aspect-ratio: 4/3) {
  .tracklist-header {
    flex-direction: column;
    align-items: stretch;
    gap: var(--space-04);
  }

  .tracklist-cover {
    width: 140px;
    height: 140px;
  }
}
</style>
