<template>
  <div class="album-view">
    <MessageContent v-if="loading && !album" loading :title="t('musicLibrary.loading')" />
    <MessageContent v-else-if="!album" :title="t('musicLibrary.notFound')" />

    <template v-else>
      <TracklistHeader
        :cover-id="album.coverArt"
        :title="album.name"
        :subtitle="subtitle"
        show-favorite
        :is-favorite="albumStarred"
        @play="playFrom(0)"
        @shuffle="shufflePlay"
        @toggle-favorite="store.toggleStar('album', album.id, album.starred)"
      />

      <div class="tracks">
        <TrackRow
          v-for="(song, idx) in songs"
          :key="song.id"
          :song="song"
          :number="song.track || idx + 1"
          :current="song.id === store.currentTrackId"
          :playing="store.isPlaying"
          @play="playFrom(idx)"
        />
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import { totalMinutes } from '../format.js';
import MessageContent from '@/components/ui/MessageContent.vue';
import TracklistHeader from '../cards/TracklistHeader.vue';
import TrackRow from '../cards/TrackRow.vue';

const props = defineProps({
  albumId: {
    type: String,
    required: true,
  },
});

const { t } = useI18n();
const store = useMusicLibraryStore();

const album = ref(null);
const loading = ref(false);

const songs = computed(() => album.value?.song || []);

const albumStarred = computed(() =>
  album.value ? store.isStarred('album', album.value.id, album.value.starred) : false
);

const subtitle = computed(() => {
  if (!album.value) return '';
  const parts = [];
  if (album.value.artist) parts.push(album.value.artist);
  if (album.value.year) parts.push(String(album.value.year));
  parts.push(t('musicLibrary.tracksCount', { count: album.value.songCount || songs.value.length }));
  const mins = totalMinutes(album.value.duration);
  if (mins) parts.push(`${mins} ${t('musicLibrary.minutesShort')}`);
  return parts.join(' · ');
});

function playFrom(index) {
  store.playContext(songs.value, index, false);
}

function shufflePlay() {
  if (!songs.value.length) return;
  // Pin a random track first, backend shuffles the remainder → true shuffle feel.
  const start = Math.floor(Math.random() * songs.value.length);
  store.playContext(songs.value, start, true);
}

watch(() => props.albumId, async (id) => {
  loading.value = true;
  album.value = await store.fetchAlbum(id);
  loading.value = false;
}, { immediate: true });
</script>

<style scoped>
.album-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.tracks {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}
</style>
