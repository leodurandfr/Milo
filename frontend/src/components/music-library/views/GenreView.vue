<template>
  <div class="genre-view">
    <MessageContent v-if="loading && !songs.length" loading :title="t('musicLibrary.loading')" />
    <MessageContent v-else-if="!songs.length" :title="t('musicLibrary.noTracks')" />

    <template v-else>
      <div class="genre-actions">
        <p class="genre-count text-mono">{{ t('musicLibrary.tracksCount', { count: songs.length }) }}</p>
        <div class="genre-buttons">
          <Button variant="brand" left-icon="play" @click="playFrom(0)">{{ t('musicLibrary.play') }}</Button>
          <Button variant="background-strong" @click="shufflePlay">{{ t('musicLibrary.shuffle') }}</Button>
        </div>
      </div>

      <div class="tracks">
        <TrackRow
          v-for="(song, idx) in songs"
          :key="song.id"
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
import { ref, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import MessageContent from '@/components/ui/MessageContent.vue';
import Button from '@/components/ui/Button.vue';
import TrackRow from '../cards/TrackRow.vue';

const props = defineProps({
  genre: {
    type: String,
    required: true,
  },
});

const { t } = useI18n();
const store = useMusicLibraryStore();

const songs = ref([]);
const loading = ref(false);

function playFrom(index) {
  store.playContext(songs.value, index, false);
}

function shufflePlay() {
  if (!songs.value.length) return;
  const start = Math.floor(Math.random() * songs.value.length);
  store.playContext(songs.value, start, true);
}

watch(() => props.genre, async (genre) => {
  loading.value = true;
  songs.value = await store.fetchGenreSongs(genre);
  loading.value = false;
}, { immediate: true });
</script>

<style scoped>
.genre-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.genre-actions {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-03);
  flex-wrap: wrap;
}

.genre-count {
  margin: 0;
  color: var(--color-text-secondary);
}

.genre-buttons {
  display: flex;
  flex-direction: row;
  gap: var(--space-02);
}

.tracks {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}
</style>
