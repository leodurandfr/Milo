<template>
  <div class="liked-songs-view">
    <div class="swap-stack">
      <Transition name="fade-slide">
        <SkeletonDetailPage v-if="store.likedSongsLoading && !store.likedSongs.length" key="loading" cover artist />
        <MessageContent v-else-if="!store.likedSongs.length" key="empty" icon="musicNote" :title="t('musicLibrary.noTracks')" />

        <div v-else key="loaded" class="content-stack">
          <DetailHeader
            liked
            :title="t('musicLibrary.playlists.likedSongs')"
            :subtitle-meta="subtitle"
            :playing="header.playing.value"
            :play-loading="header.loading.value"
            @play="header.play"
            @pause="header.pause"
          />

          <TrackList>
            <TrackRow
              v-for="(song, idx) in visibleSongs"
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
            <div v-if="hasMore" ref="sentinelRef" aria-hidden="true"></div>
          </TrackList>
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import MessageContent from '@/components/ui/MessageContent.vue';
import DetailHeader from '@/components/audio/DetailHeader.vue';
import SkeletonDetailPage from '@/components/audio/SkeletonDetailPage.vue';
import TrackRow from '@/components/audio/TrackRow.vue';
import TrackList from '@/components/audio/TrackList.vue';
import { useRenderWindow } from '@/composables/useRenderWindow';
import { useDetailPlayback } from '@/composables/useDetailPlayback';

const { t } = useI18n();
const store = useMusicLibraryStore();

const { visible: visibleSongs, hasMore, sentinelRef } = useRenderWindow(() => store.likedSongs);

const subtitle = computed(() => t('musicLibrary.tracksCount', { count: store.likedSongsCount }));

function playFrom(index) {
  return store.playContext(store.likedSongs, index, false, 'liked');
}

const header = useDetailPlayback(
  'music_library', () => store.currentContextId === 'liked', () => playFrom(0),
);

// Started in setup, not onMounted: the loading flag is then up before the first
// render, so the empty state never flashes ahead of the spinner.
store.loadLikedSongs({ force: true });
</script>

<style scoped>
.liked-songs-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.content-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}
</style>
