<template>
  <div class="liked-songs-view">
    <div class="transition-container">
      <Transition name="content-swap">
        <MessageContent v-if="store.likedSongsLoading && !store.likedSongs.length" key="loading"
          loading :title="t('musicLibrary.loading')" />
        <MessageContent v-else-if="!store.likedSongs.length" key="empty" :title="t('musicLibrary.noTracks')" />

        <div v-else key="loaded" class="content-stack">
          <DetailHeader
            icon="heart"
            :title="t('musicLibrary.playlists.likedSongs')"
            :subtitle-meta="subtitle"
            @play="playFrom(0)"
            @shuffle="shufflePlay"
          />

          <div class="tracks">
            <TrackRow
              v-for="(song, idx) in store.likedSongs"
              :key="song.id"
              :song="song"
              :number="idx + 1"
              :current="song.id === store.currentTrackId"
              :playing="store.isPlaying"
              show-artist
              show-menu
              show-cover
              :cover-url="store.thumbUrl(song.coverArt)"
              @play="playFrom(idx)"
              @menu="store.requestAddToPlaylist([song.id])"
            />
          </div>
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import MessageContent from '@/components/ui/MessageContent.vue';
import DetailHeader from '@/components/audio/DetailHeader.vue';
import TrackRow from '@/components/audio/TrackRow.vue';

const { t } = useI18n();
const store = useMusicLibraryStore();

const subtitle = computed(() => t('musicLibrary.tracksCount', { count: store.likedSongsCount }));

function playFrom(index) {
  store.playContext(store.likedSongs, index, false);
}

function shufflePlay() {
  if (!store.likedSongs.length) return;
  const start = Math.floor(Math.random() * store.likedSongs.length);
  store.playContext(store.likedSongs, start, true);
}

onMounted(() => store.loadLikedSongs({ force: true }));
</script>

<style scoped>
.liked-songs-view {
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

.content-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.tracks {
  display: flex;
  flex-direction: column;
  gap: 0;
}
</style>
