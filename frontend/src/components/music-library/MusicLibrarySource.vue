<template>
  <AudioSourceLayout ref="audioLayoutRef" :show-player="shouldShowPlayer"
    :header-title="currentTitle" :header-show-back="canGoBack"
    header-icon="music_library" header-variant="background-neutral"
    :header-actions-key="currentView" :content-key="currentView"
    :player-mobile-height="184" :pending-scroll-restore="pendingScrollRestore"
    @header-back="goBack" @scroll-restored="onScrollRestored">

    <!-- Header actions (home only): search -->
    <template v-if="currentView === 'home'" #header-actions="{ iconVariant }">
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
        :artwork="playerArtwork" :fallback-name="playerFallbackName"
        :title="playerTitle" :subtitle="playerArtist"
        :is-playing="isPlaying" :is-loading="isBuffering">
        <template #progress>
          <div @click.stop>
            <ProgressBar :current-position="currentPositionSec" :duration="currentDurationSec"
              :progress-percentage="livePercent" @seek="handleSeek" />
          </div>
        </template>

        <template #controls>
          <div class="ml-controls" @click.stop>
            <div class="playback-controls">
              <IconButton icon="previous" variant="on-dark" size="small" @click="store.previous()" />
              <IconButton :icon="isPlaying ? 'pause' : 'play'" variant="on-dark" size="medium"
                :loading="isBuffering" @click="togglePlayPause" />
              <IconButton icon="next" variant="on-dark" size="small" @click="store.next()" />
            </div>
            <div class="ml-secondary">
              <IconButton :icon="store.currentStarred ? 'heart' : 'heartOff'" variant="on-dark" size="small"
                @click="store.toggleCurrentStar()" />
              <IconButton icon="queue" variant="on-dark" size="small" @click="goToQueue" />
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
const playerFallbackName = computed(() => store.displayTrack?.album || store.displayTrack?.title || '');

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
::-webkit-scrollbar {
  display: none;
}

/* Docked-player controls: transport row + favorite/queue row. */
.ml-controls {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-03);
  width: 100%;
}

.ml-secondary {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  gap: var(--space-05);
}
</style>
