<template>
  <div class="playlist-view">
    <MessageContent v-if="loading && !playlist" loading :title="t('musicLibrary.loading')" />
    <MessageContent v-else-if="!playlist" :title="t('musicLibrary.notFound')" />

    <template v-else>
      <TracklistHeader
        :cover-id="playlist.coverArt"
        :title="playlist.name"
        :subtitle="subtitle"
        @play="playFrom(0)"
        @shuffle="shufflePlay"
      />

      <div class="tracks">
        <TrackRow
          v-for="(song, idx) in songs"
          :key="`${song.id}-${idx}`"
          :song="song"
          :number="idx + 1"
          :current="song.id === store.currentTrackId"
          :playing="store.isPlaying"
          show-artist
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
  playlistId: {
    type: String,
    required: true,
  },
});

const { t } = useI18n();
const store = useMusicLibraryStore();

const playlist = ref(null);
const loading = ref(false);

const songs = computed(() => playlist.value?.entry || []);

const subtitle = computed(() => {
  if (!playlist.value) return '';
  const parts = [t('musicLibrary.tracksCount', { count: playlist.value.songCount || songs.value.length })];
  const mins = totalMinutes(playlist.value.duration);
  if (mins) parts.push(`${mins} ${t('musicLibrary.minutesShort')}`);
  return parts.join(' · ');
});

function playFrom(index) {
  store.playContext(songs.value, index, false);
}

function shufflePlay() {
  if (!songs.value.length) return;
  const start = Math.floor(Math.random() * songs.value.length);
  store.playContext(songs.value, start, true);
}

watch(() => props.playlistId, async (id) => {
  loading.value = true;
  playlist.value = await store.fetchPlaylist(id);
  loading.value = false;
}, { immediate: true });
</script>

<style scoped>
.playlist-view {
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
