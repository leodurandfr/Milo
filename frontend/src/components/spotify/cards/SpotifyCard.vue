<template>
  <div v-press class="playlist-card" @click="$emit('click')">
    <LazyImage :src="item.image || ''" :fallback="musicPlaceholder" :alt="title" lazy skeleton
      class="playlist-cover" :class="{ round: item.kind === 'artist' }" />
    <div class="playlist-info">
      <p class="playlist-name heading-4">{{ title }}</p>
      <p v-if="byline" class="playlist-owner text-mono-medium">{{ byline }}</p>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import LazyImage from '@/components/ui/LazyImage.vue';
import { musicPlaceholder } from '@/constants/placeholders';

const props = defineProps({
  // A card as /api/spotify/home lists it: one of Spotify's home ({ uri, kind,
  // name, subtitle, image }) or a library playlist ({ uri, name, owner, image }).
  // No track count: the library's is wrong for every playlist Spotify
  // regenerates.
  item: {
    type: Object,
    required: true,
  },
});

defineEmits(['click']);

const { t } = useI18n();

const title = computed(() => props.item.name || t('spotify.untitledPlaylist'));
const byline = computed(() => {
  if (props.item.subtitle) return props.item.subtitle;
  if (props.item.kind === 'artist') return t('spotify.artist');
  return props.item.owner === 'spotify' ? 'Spotify' : '';
});
</script>

<style scoped>
.playlist-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  cursor: pointer;
  min-width: 0;
}

.playlist-cover {
  aspect-ratio: 1;
  width: 100%;
  border-radius: var(--radius-02);
  background: var(--color-surface-glass);
}

.playlist-cover.round {
  border-radius: var(--radius-full);
}




.playlist-info {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  min-width: 0;
}

.playlist-name,
.playlist-owner {
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.playlist-name {
  color: var(--color-text);
}

.playlist-owner {
  color: var(--color-text-secondary);
}
</style>
