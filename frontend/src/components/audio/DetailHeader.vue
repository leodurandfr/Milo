<template>
  <div class="detail-header">
    <div v-if="liked" class="detail-header-cover">
      <LikedCover />
    </div>
    <LazyImage
      v-else
      :src="imageSrc"
      :fallback="fallback"
      :alt="title"
      priority="high"
      class="detail-header-cover"
    />

    <div class="detail-header-meta">
      <div class="detail-header-titles">
        <h2 class="detail-header-title heading-2">{{ title }}</h2>
        <p v-if="subtitle || artistLinks" class="detail-header-subtitle heading-3"
          :class="{ 'detail-header-subtitle--clickable': subtitleClickable }"
          @click="subtitleClickable && $emit('select-artist')">
          <ArtistNames v-if="artistLinks" :artists="subtitleArtists" @open="$emit('select-artist', $event)" />
          <template v-else>{{ subtitle }}</template>
        </p>
        <p v-if="subtitleMeta" class="detail-header-metaline text-mono-medium">{{ subtitleMeta }}</p>
      </div>

      <div v-if="hasActions" class="detail-header-actions">
        <!-- Extra actions (e.g. the playlist Edit/Done toggle, or podcast Subscribe/Unsubscribe). -->
        <slot name="actions"></slot>
        <IconButton v-if="showShuffle" icon="shuffle" variant="on-contrast" size="medium"
          :aria-label="t('musicLibrary.shuffle')" @click="$emit('shuffle')" />
        <Button v-if="showPlay" variant="brand" size="medium" left-icon="play" :loading="playLoading"
          @click="$emit('play')">{{ t('musicLibrary.play') }}</Button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, useSlots } from 'vue';
import { useI18n } from '@/services/i18n';
import LazyImage from '@/components/ui/LazyImage.vue';
import IconButton from '@/components/ui/IconButton.vue';
import Button from '@/components/ui/Button.vue';
import ArtistNames from '@/components/audio/ArtistNames.vue';
import LikedCover from '@/components/audio/LikedCover.vue';

const props = defineProps({
  imageSrc: {
    type: String,
    default: '',
  },
  fallback: {
    type: String,
    default: '',
  },
  // The Liked Songs header: its cover in place of cover art.
  liked: {
    type: Boolean,
    default: false,
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
  // The whole subtitle as one link; `select-artist` then carries nothing.
  subtitleClickable: {
    type: Boolean,
    default: false,
  },
  // The subtitle drawn name by name, `{ name, link }` (ArtistNames), in place
  // of `subtitle` once one has a page: each such name is its own link, and
  // `select-artist` carries its index.
  subtitleArtists: {
    type: Array,
    default: () => [],
  },
  showPlay: {
    type: Boolean,
    default: true,
  },
  // Spinner on the play button, for a header whose queue is only assembled on
  // the press (the artist page fetches one album per release).
  playLoading: {
    type: Boolean,
    default: false,
  },
  showShuffle: {
    type: Boolean,
    default: true,
  },
});

defineEmits(['play', 'shuffle', 'select-artist']);

const { t } = useI18n();
const slots = useSlots();

const artistLinks = computed(() => props.subtitleArtists.some((artist) => artist.link));

const hasActions = computed(
  () => props.showPlay || props.showShuffle || !!slots.actions
);
</script>

<style scoped>
.detail-header {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: var(--space-03);
  background: var(--color-contrast);
  border-radius: var(--radius-04);
  padding: var(--space-03) var(--space-04) var(--space-03) var(--space-03);
}

.detail-header-cover {
  flex-shrink: 0;
  width: 150px;
  height: 150px;
  border-radius: var(--radius-02);
}

.detail-header-meta {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  gap: var(--space-04);
}

.detail-header-titles {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  min-width: 0;
}

.detail-header-title {
  margin: 0;
  color: var(--color-text-on-contrast);
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.detail-header-subtitle {
  margin: 0;
  width: fit-content;
  max-width: 100%;
  color: var(--color-brand);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.detail-header-subtitle--clickable {
  cursor: pointer;
}

.detail-header-metaline {
  margin: 0;
  color: var(--color-text-on-contrast-secondary);
}

.detail-header-actions {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: var(--space-02);
  flex-shrink: 0;
}

@media (max-aspect-ratio: 4/3) {
  .detail-header {
    flex-direction: column;
    align-items: stretch;
  }

  .detail-header-cover {
    width: 100%;
    height: auto;
    aspect-ratio: 1 / 1;
  }
}
</style>
