<template>
  <div class="music-library-source">
    <AudioSourceLayout ref="audioLayoutRef" :show-player="shouldShowPlayer"
      :header-title="currentTitle" :header-show-back="canGoBack"
      header-icon="music_library" header-variant="background-neutral" gradient="music-library"
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
          @select-genre="openGenre" @select-playlist="openPlaylist" />

        <AlbumView v-else-if="currentView === 'album'" key="album" :album-id="currentParams.albumId" />

        <ArtistView v-else-if="currentView === 'artist'" key="artist" :artist-id="currentParams.artistId"
          @select-album="openAlbum" />

        <GenreView v-else-if="currentView === 'genre'" key="genre" :genre="currentParams.genre" />

        <PlaylistView v-else-if="currentView === 'playlist'" key="playlist"
          :playlist-id="currentParams.playlistId" @deleted="goBack" />

        <SearchView v-else-if="currentView === 'search'" key="search"
          @select-album="openAlbum" @select-artist="openArtist" />

        <QueueView v-else-if="currentView === 'queue'" key="queue" />
      </template>

      <!-- Docked player -->
      <template #player>
        <AudioPlayer :visible="shouldShowPlayer" source="music_library"
          :artwork="playerArtwork" :placeholder-artwork="albumPlaceholder"
          :title="playerTitle"
          :is-playing="isPlaying" :is-loading="isBuffering" swipe-enabled
          :tracks="store.queue" :current-index="store.queueIndex"
          @swipe-next="store.next()" @swipe-prev="store.swipePrevious()">
          <!-- Track info (desktop + non-swipe fallback). On mobile the swipe
               carousel renders its own title/artist from the queue (see AudioPlayer). -->
          <template #info>
            <PlayerInfoText class="desktop-only" :title="playerTitle" :secondary="playerArtist" />
            <p class="player-title text-body mobile-only">{{ playerTitle }}</p>
            <p v-if="playerArtist" class="player-subtitle text-body mobile-only">{{ playerArtist }}</p>
          </template>

          <template #progress>
            <div @click.stop>
              <ProgressBar :current-position="currentPositionSec" :duration="currentDurationSec"
                :progress-percentage="livePercent" @seek="handleSeek" />
            </div>
          </template>

          <!-- Mobile keeps only play/pause; prev/next/shuffle/like are desktop-only —
               the mini-player's swipe gesture covers next (right) / prev (left), the
               rest move into the future expanded mini-player view. -->
          <template #controls>
            <div class="ml-controls" @click.stop>
              <div class="playback-controls">
                <IconButton icon="shuffle" variant="on-dark" size="small" class="ml-transport-extra"
                  :color="store.shuffle ? 'var(--color-brand)' : undefined"
                  @click="store.toggleShuffle()" />
                <div class="ml-transport-main">
                  <IconButton icon="previous" variant="on-dark" size="small" class="ml-transport-extra"
                    @click="store.previous()" />
                  <IconButton :icon="isPlaying ? 'pause' : 'play'" variant="on-dark" size="medium"
                    :loading="isBuffering" @click="togglePlayPause" />
                  <IconButton icon="next" variant="on-dark" size="small" class="ml-transport-extra"
                    @click="store.next()" />
                </div>
                <IconButton :icon="store.currentStarred ? 'heart' : 'heartOff'" variant="on-dark" size="small"
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
import ProgressBar from './ProgressBar.vue';

import LibraryHome from './views/LibraryHome.vue';
import AlbumView from './views/AlbumView.vue';
import ArtistView from './views/ArtistView.vue';
import GenreView from './views/GenreView.vue';
import PlaylistView from './views/PlaylistView.vue';
import SearchView from './views/SearchView.vue';
import QueueView from './views/QueueView.vue';
import AddToPlaylistModal from './AddToPlaylistModal.vue';

const store = useMusicLibraryStore();
const { t } = useI18n();
const timer = useTimer();

// Scroll-aware navigation stack (save/restore across push/back).
const audioLayoutRef = ref(null);
const layoutScrollRef = computed(() => audioLayoutRef.value?.$el ?? null);
const { currentView, currentParams, canGoBack, push, back, pendingScrollRestore } =
  useNavigationStack('home', { scrollElRef: layoutScrollRef });

// Player visibility follows backend source_state (active → shown, waiting →
// hidden). Clear the sticky display track only after the fade-out completes so
// the artwork/title survive the animation.
const { isPlaying, isBuffering, shouldShowPlayer } =
  useSourcePlaybackVisibility('music_library', {
    onFadeOutStart: () => {
      timer.setTimeout(() => store.clearDisplayTrack(), 600);
    },
  });

// Live position with local interpolation (ms), rendered in seconds.
const { duration: durationMs, currentPosition: positionMs, progressPercentage: livePercent, seekTo } =
  useSourceProgress('music_library');
const currentPositionSec = computed(() => Math.floor((positionMs.value || 0) / 1000));
const currentDurationSec = computed(() => Math.floor((durationMs.value || 0) / 1000));

// === Player display (sticky through fade-out) ===
const playerTitle = computed(() => store.displayTrack?.title || '');
const playerArtist = computed(() => store.displayTrack?.artist || '');
const playerArtwork = computed(() => store.displayTrack?.albumArtUrl || null);

// === Header title per view ===
const currentTitle = computed(() => {
  switch (currentView.value) {
    case 'album': return currentParams.value.albumName || t('musicLibrary.album');
    case 'artist': return currentParams.value.artistName || t('musicLibrary.artist');
    case 'genre': return currentParams.value.genreLabel || t('musicLibrary.genre');
    case 'playlist': return currentParams.value.playlistName || t('musicLibrary.playlist');
    case 'search': return t('musicLibrary.search');
    case 'queue': return t('musicLibrary.queue');
    default: return t('audioSources.musicLibrary');
  }
});

// === Navigation ===
function openAlbum(album) {
  push('album', { albumId: album.id, albumName: album.name });
}
function openArtist(artist) {
  push('artist', { artistId: artist.id, artistName: artist.name });
}
function openGenre(genre) {
  push('genre', { genre: genre.value, genreLabel: genre.value });
}
function openPlaylist(playlist) {
  push('playlist', { playlistId: playlist.id, playlistName: playlist.name });
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

// === First-scan progress polling ===
// A fresh library scan takes minutes; poll scan-status while it runs (or while
// the catalog still looks empty — so a scan kicked off by a USB key inserted
// mid-browse is noticed) and quietly stop once the library is populated and
// idle. The tick is a cheap reactive check; the network call only fires when
// building or empty. On the completion edge, resync() reloads whichever lists
// are cached so the freshly-indexed catalog appears without a manual refresh.
const SCAN_POLL_INTERVAL_MS = 2500;
const libraryLooksEmpty = computed(() => store.albumsLoaded && !store.albums.length);

onMounted(() => {
  store.refreshScanStatus();
  timer.setInterval(() => {
    if (store.isScanning || libraryLooksEmpty.value) store.refreshScanStatus();
  }, SCAN_POLL_INTERVAL_MS);
});

watch(() => store.isScanning, (scanning, wasScanning) => {
  if (wasScanning && !scanning) store.resync();
});

// === Player controls ===
function togglePlayPause() {
  if (isPlaying.value) store.pause();
  else store.resume();
}
function handleSeek(positionSec) {
  seekTo(positionSec * 1000);
}
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

/* Docked-player controls: single transport row (shuffle … prev·play·next … like). */
.ml-controls {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-03);
  width: 100%;
}

/* Centered transport trio (prev · play · next). */
.ml-transport-main {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-02);
}

/* Desktop: shuffle pinned far-left, like far-right, trio centered between.
   Mobile keeps the trio centered (shuffle/like are hidden there). */
@media (min-aspect-ratio: 4/3) {
  .ml-controls .playback-controls {
    justify-content: space-between;
    padding: 0 var(--space-01);
  }
}
</style>
