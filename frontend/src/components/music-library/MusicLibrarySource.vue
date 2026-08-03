<template>
  <div class="music-library-source">
    <AudioSourceLayout ref="audioLayoutRef" :show-player="shouldShowPlayer"
      :header-title="currentTitle" :header-show-back="canGoBack" :header-title-muted="detailsTitleView"
      header-icon="music_library" header-variant="background-neutral" gradient="music_library"
      :header-actions-key="currentView" :content-key="currentView"
      :player-mobile-height="144" :pending-scroll-restore="pendingScrollRestore"
      @header-back="goBack" @scroll-restored="onScrollRestored">

      <!-- Header actions (home only): queue + search -->
      <template v-if="currentView === 'home'" #header-actions="{ iconVariant }">
        <IconButton icon="queue" :variant="iconVariant" @click="goToQueue" />
        <IconButton icon="search" :variant="iconVariant" @click="goToSearch" />
      </template>

      <!-- Scrollable views -->
      <template #content>
        <LibraryHome v-if="currentView === 'home'" key="home"
          @select-album="openAlbum" @select-artist="openArtist"
          @select-genre="openGenre" @select-playlist="openPlaylist"
          @select-liked="openLikedSongs" />

        <AlbumView v-else-if="currentView === 'album'" key="album" :album-id="currentParams.albumId"
          @select-artist="openArtist" />

        <ArtistView v-else-if="currentView === 'artist'" key="artist" :artist-id="currentParams.artistId"
          @select-album="openAlbum" />

        <GenreView v-else-if="currentView === 'genre'" key="genre" :genre="currentParams.genre"
          @select-album="openAlbum" />

        <PlaylistView v-else-if="currentView === 'playlist'" key="playlist"
          :playlist-id="currentParams.playlistId" @deleted="goBack" />

        <SearchView v-else-if="currentView === 'search'" key="search"
          @select-album="openAlbum" @select-artist="openArtist" />

        <QueueView v-else-if="currentView === 'queue'" key="queue" />

        <LikedSongsView v-else-if="currentView === 'liked'" key="liked" />
      </template>

      <!-- Docked player -->
      <template #player>
        <AudioPlayer :visible="shouldShowPlayer" source="music_library"
          :artwork="playerArtwork" :placeholder-artwork="albumPlaceholder"
          :title="playerTitle"
          :is-playing="isPlaying" :is-loading="isBuffering" swipe-enabled
          :tracks="store.queue" :current-index="store.queueIndex"
          @swipe-next="store.next()" @swipe-prev="store.swipePrevious()"
          @artwork-click="openPlayerAlbum" @secondary-click="openPlayerArtist">
          <!-- Track info: PlayerInfoText's vertical layout renders both the desktop
               sidebar and the expanded full-screen sheet (nothing hides .vertical-layout
               inside the expanded card for this source — same as podcast). On mobile the
               docked bar never reaches this slot — the swipe carousel renders its
               own title/artist from the queue (see AudioPlayer). -->
          <template #info>
            <PlayerInfoText class="vertical-layout" :title="playerTitle" :secondary="playerArtist" />
          </template>

          <template #progress>
            <div @click.stop>
              <ProgressBar :current-position="positionMs" :duration="durationMs"
                :progress-percentage="livePercent" variant="dark" @seek="seekTo" />
            </div>
          </template>

          <!-- Mobile keeps only play/pause; prev/next/shuffle/like are desktop-only —
               the mini-player's swipe gesture covers next (right) / prev (left), the
               rest move into the future expanded mini-player view. -->
          <template #controls>
            <div class="ml-controls" @click.stop>
              <div class="playback-controls">
                <IconButton icon="shuffle" variant="ghost" size="small" class="ml-transport-extra"
                  :color="store.shuffle ? 'var(--color-text-contrast)' : 'var(--color-text-contrast-50)'"
                  @click="store.toggleShuffle()" />
                <div class="ml-transport-main">
                  <IconButton icon="previous" variant="ghost" size="small" class="ml-transport-extra"
                    @click="store.previous()" />
                  <IconButton :icon="isPlaying ? 'pause' : 'play'" variant="ghost" size="medium"
                    :loading="isBuffering" @click="togglePlayPause" />
                  <IconButton icon="next" variant="ghost" size="small" class="ml-transport-extra"
                    :disabled="!hasNext" @click="store.next()" />
                </div>
                <IconButton :icon="store.currentStarred ? 'heart' : 'heartOff'" variant="ghost" size="small"
                  :color="store.currentStarred ? 'var(--color-text-contrast)' : 'var(--color-text-contrast-50)'"
                  class="ml-transport-extra" @click="store.toggleCurrentStar()" />
              </div>
            </div>
          </template>
        </AudioPlayer>
      </template>
    </AudioSourceLayout>

    <!-- Add-to-playlist picker, opened from any track row's ⋯ menu (store-driven). -->
    <AddToPlaylistModal
      :is-open="!!store.addToPlaylistSongIds"
      :song-ids="store.addToPlaylistSongIds || []"
      @close="store.closeAddToPlaylist()"
    />
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import { useNavigationStack } from '@/composables/useNavigationStack';
import { useSourcePlaybackVisibility } from '@/composables/useSourcePlaybackVisibility';
import { useSourceProgress } from '@/composables/useSourceProgress';
import { useTimer } from '@/composables/useTimer';
import { useI18n } from '@/services/i18n';
import IconButton from '@/components/ui/IconButton.vue';
import AudioPlayer from '@/components/audio/AudioPlayer.vue';
import AudioSourceLayout from '@/components/audio/AudioSourceLayout.vue';
import PlayerInfoText from '@/components/audio/PlayerInfoText.vue';
import albumPlaceholder from '@/assets/images/album-placeholder.svg';
import ProgressBar from '@/components/audio/ProgressBar.vue';

import LibraryHome from './views/LibraryHome.vue';
import AlbumView from './views/AlbumView.vue';
import ArtistView from './views/ArtistView.vue';
import GenreView from './views/GenreView.vue';
import PlaylistView from './views/PlaylistView.vue';
import SearchView from './views/SearchView.vue';
import QueueView from './views/QueueView.vue';
import LikedSongsView from './views/LikedSongsView.vue';
import AddToPlaylistModal from './AddToPlaylistModal.vue';

const store = useMusicLibraryStore();
const { t } = useI18n();
const timer = useTimer();

// Scroll-aware navigation stack (save/restore across push/back).
const audioLayoutRef = ref(null);
const layoutScrollRef = computed(() => audioLayoutRef.value?.$el ?? null);
const { currentView, currentParams, canGoBack, push, back, pendingScrollRestore } =
  useNavigationStack('home', { scrollElRef: layoutScrollRef });

// Player visibility follows backend source_state (active → shown, ready →
// hidden). Clear the sticky display track only after the fade-out completes so
// the artwork/title survive the animation.
const { isPlaying, isBuffering, shouldShowPlayer } =
  useSourcePlaybackVisibility('music_library', {
    onFadeOutStart: () => {
      timer.setTimeout(() => store.clearDisplayTrack(), 600);
    },
  });

// Live position with local interpolation (ms).
const { duration: durationMs, currentPosition: positionMs, progressPercentage: livePercent, seekTo } =
  useSourceProgress('music_library');

// === Player display (sticky through fade-out) ===
const playerTitle = computed(() => store.displayTrack?.title || '');
const playerArtist = computed(() => store.displayTrack?.artist || '');
const playerArtwork = computed(() => store.displayTrack?.albumArtUrl || null);

// === Header title per view ===
const currentTitle = computed(() => {
  switch (currentView.value) {
    case 'album': return t('musicLibrary.albumDetails');
    case 'artist':
      return currentParams.value.artistAlbumCount === 1
        ? t('musicLibrary.artistAlbum', { artist: currentParams.value.artistName })
        : t('musicLibrary.artistAlbums', { artist: currentParams.value.artistName });
    case 'genre': return currentParams.value.genreLabel || t('musicLibrary.genre');
    case 'playlist': return t('musicLibrary.playlistDetails');
    case 'liked': return t('musicLibrary.playlists.likedSongs');
    case 'search': return t('musicLibrary.search');
    case 'queue': return t('musicLibrary.queue');
    default: return t('audioSources.musicLibrary');
  }
});

const detailsTitleView = computed(() =>
  ['album', 'playlist'].includes(currentView.value)
);

// === Navigation ===
function openAlbum(album) {
  push('album', { albumId: album.id, albumName: album.name });
}
function openArtist(artist) {
  push('artist', { artistId: artist.id, artistName: artist.name, artistAlbumCount: artist.albumCount });
}
function openGenre(genre) {
  push('genre', { genre: genre.value, genreLabel: genre.value });
}
function openPlaylist(playlist) {
  push('playlist', { playlistId: playlist.id, playlistName: playlist.name });
}
function openLikedSongs() {
  push('liked');
}
// From the docked player's artwork/artist-line clicks — only the currently
// displayed track's ids are known here (no album/artist track counts).
function openPlayerAlbum() {
  const albumId = store.displayTrack?.albumId;
  if (albumId) openAlbum({ id: albumId, name: store.displayTrack.album });
}
function openPlayerArtist() {
  const artistId = store.displayTrack?.artistId;
  if (artistId) openArtist({ id: artistId, name: store.displayTrack.artist });
}
function goToSearch() {
  push('search');
}
function goToQueue() {
  push('queue');
}
function goBack() {
  back();
}
function onScrollRestored() {
  pendingScrollRestore.value = null;
}

// Scan progress needs nothing here any more: the backend watches Navidrome and
// pushes the flag with the storage list, and the store reloads its cached lists
// on the completion edge — so a key plugged in mid-browse fills the view on its
// own, from one watcher for the whole appliance instead of one per open tab.
onMounted(() => {
  store.loadLikedSongs();
});

// === Player controls ===
function togglePlayPause() {
  if (isPlaying.value) store.pause();
  else store.resume();
}
// Mirrors backend's 'next' no-op on the queue's last track.
const hasNext = computed(() => store.queueIndex >= 0 && store.queueIndex < store.queue.length - 1);
</script>

<style scoped>
/* Fills the transition slot; AudioSourceLayout inside is width/height 100%. */
.music-library-source {
  width: 100%;
  height: 100%;
}

::-webkit-scrollbar {
  display: none;
}

/* The .ml-controls / .ml-transport-main layout lives in AudioPlayer.vue, in
   :deep() — this row is slotted into it, and the same row is re-authored by the
   gallery's SourceStage, which scoped CSS here could never reach. */
</style>
