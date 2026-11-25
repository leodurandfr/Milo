<template>
  <div class="liked-songs-view">
    <div class="swap-stack">
      <Transition name="fade-slide">
        <MessageContent v-if="store.likedSongsError && !store.likedSongs.length" key="unavailable" icon="musicNote"
          :title="t(store.likedSongsError === 'starting' ? 'musicLibrary.catalogStarting' : 'musicLibrary.catalogUnreachable')"
          :subtitle="store.likedSongsError === 'starting' ? t('musicLibrary.catalogStartingHint') : ''"
          :cta-label="t('musicLibrary.retry')" cta-variant="control" :cta-loading="store.likedSongsLoading"
          :cta-click="retry" />
        <SkeletonDetailPage v-else-if="store.likedSongsLoading && !store.likedSongs.length" key="loading" cover artist />
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
          >
            <template #actions>
              <LibraryItemMenu trigger="header" :songs="() => store.likedSongs" shuffle @shuffle="header.shuffle" />
            </template>
          </DetailHeader>

          <TrackList>
            <TrackRow
              v-for="(song, idx) in visibleSongs"
              :key="song.id"
              v-memo="[song, idx, song.id === store.currentTrackId, song.id === store.currentTrackId && store.isPlaying]"
              :song="song"
              :number="idx + 1"
              :current="song.id === store.currentTrackId"
              :playing="song.id === store.currentTrackId && store.isPlaying"
              show-artist
              show-cover
              :cover-url="store.thumbUrl(song.coverArt)"
              @play="playFrom(idx)"
            >
              <template #menu>
                <LibraryItemMenu :songs="() => [song]" add-to-playlist />
              </template>
            </TrackRow>
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
import LibraryItemMenu from '../LibraryItemMenu.vue';
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
  'music_library', () => store.currentContextId === 'liked',
  (shuffle) => (shuffle ? store.playShuffled(store.likedSongs, 'liked') : playFrom(0)),
);

// A read that failed is said as the neighboring pages say it, never as "No
// tracks": a 503 while Navidrome starts, or any other failure, with a Retry —
// a failure the availability never announced is reloaded by nothing else.
const retry = () => store.loadLikedSongs({ force: true });

// Started in setup, not onMounted: the loading flag is then up before the first
// render, so the empty state never flashes ahead of the spinner.
retry();
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
