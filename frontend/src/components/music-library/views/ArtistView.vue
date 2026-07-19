<template>
  <div class="artist-view">
    <MessageContent v-if="loading && !artist" loading :title="t('musicLibrary.loading')" />
    <MessageContent v-else-if="!artist" :title="t('musicLibrary.notFound')" />

    <template v-else>
      <div class="albums-grid">
        <AlbumCard
          v-for="album in albums"
          :key="album.id"
          :album="album"
          @click="$emit('select-album', album)"
        />
      </div>
    </template>
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

.albums-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: var(--space-04);
}
</style>
