<template>
  <div class="artist-view">
    <div class="transition-container">
      <Transition name="content-swap">
        <MessageContent v-if="loading && !artist" key="loading" loading :title="t('musicLibrary.loading')" />
        <MessageContent v-else-if="!artist" key="notfound" :title="t('musicLibrary.notFound')" />
        <div v-else key="loaded" class="albums-grid">
          <AlbumCard v-for="album in albums" :key="album.id" :album="album" @click="$emit('select-album', album)" />
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import MessageContent from '@/components/ui/MessageContent.vue';
import AlbumCard from '../cards/AlbumCard.vue';

const props = defineProps({
  artistId: {
    type: String,
    required: true,
  },
});

defineEmits(['select-album']);

const { t } = useI18n();
const store = useMusicLibraryStore();

const artist = ref(null);
const loading = ref(false);

const albums = computed(() => artist.value?.album || []);

watch(() => props.artistId, async (id) => {
  loading.value = true;
  artist.value = await store.fetchArtist(id);
  loading.value = false;
}, { immediate: true });
</script>

<style scoped>
.artist-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.transition-container {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
}

.transition-container > * {
  grid-row: 1;
  grid-column: 1;
  align-self: start;
}

.albums-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  row-gap: var(--space-05);
  column-gap: var(--space-04);
}

@media (max-aspect-ratio: 4/3) {
  .albums-grid {
    grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
    column-gap: var(--space-03);
  }
}
</style>
