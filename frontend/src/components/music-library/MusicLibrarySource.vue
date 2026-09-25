<template>
  <div class="music-library-source">
    <AudioSourceLayout ref="audioLayoutRef" :show-player="shouldShowPlayer"
      :header-title="currentTitle" :header-show-back="canGoBack" :header-title-muted="detailsTitleView"
      header-icon="music_library" header-variant="background-neutral" gradient="music_library"
      :header-actions-key="currentView" :content-key="currentView"
      :player-mobile-height="144" :pending-scroll-restore="pendingScrollRestore"
      @header-back="goBack" @scroll-restored="onScrollRestored">

      <!-- Header actions (home only): queue + search. Search is scoped on the
           selected storage space, so it goes away with it (see scopedViews
           below); the queue is what is loaded, not what is browsable, and stays. -->
      <template v-if="currentView === 'home'" #header-actions="{ iconVariant }">
        <IconButton icon="queue" :variant="iconVariant" @click="goToQueue" />
        <IconButton v-if="!store.disconnectedStorage" icon="search" :variant="iconVariant"
          @click="goToSearch" />
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
        <AudioPlayer :visible="shouldShowPlayer" source="music_library" @after-hide="onAfterHide"
          :artwork="playerArtwork"
          :title="playerTitle"
          swipe-enabled
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
                :progress-percentage="livePercent" :interactive="store.canSend('seek')"
                variant="dark" @seek="seekTo" />
            </div>
          </template>

          <!-- Mobile keeps only play/pause; prev/next/shuffle/like are desktop-only —
               the mini-player's swipe gesture covers next (right) / prev (left), the
               rest move into the future expanded mini-player view. -->
          <template #controls>
            <div class="ml-controls" @click.stop>
              <div class="playback-controls">
                <IconButton icon="shuffle" variant="ghost" size="small" class="ml-transport-extra transport-secondary-round"
                  :color="store.shuffle ? 'var(--color-text-contrast)' : 'var(--color-text-contrast-50)'"
                  :disabled="!store.canSend('set_shuffle')" @click="store.toggleShuffle()" />
                <div class="ml-transport-main">
                  <IconButton icon="previous" variant="ghost" size="small" class="ml-transport-extra transport-secondary"
                    :disabled="!store.canSend('prev')" @click="store.previous()" />
                  <IconButton :icon="isPlaying ? 'pause' : 'play'" variant="ghost" size="medium"
                    class="transport-primary" :loading="isBuffering" @click="togglePlayPause" />
                  <IconButton icon="next" variant="ghost" size="small" class="ml-transport-extra transport-secondary"
                    :disabled="!store.canSend('next')" @click="store.next()" />
                </div>
                <IconButton :icon="store.currentStarred ? 'heart' : 'heartOff'" variant="ghost" size="small"
                  :color="store.currentStarred ? 'var(--color-text-contrast)' : 'var(--color-text-contrast-50)'"
                  class="ml-transport-extra transport-secondary-round" @click="store.toggleCurrentStar()" />
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
import { useI18n } from '@/services/i18n';
import IconButton from '@/components/ui/IconButton.vue';
import AudioPlayer from '@/components/audio/AudioPlayer.vue';
import AudioSourceLayout from '@/components/audio/AudioSourceLayout.vue';
import PlayerInfoText from '@/components/audio/PlayerInfoText.vue';
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

// Opening the library always lands on Albums. The tab is store state so it
// survives navigating into an album and back — which also makes it outlive this
// component, and coming back tomorrow on whichever tab was last touched is not
// where anyone left off. Reset in setup rather than onMounted: children mount
// first, so LibraryHome would otherwise fetch the stale tab before the reset.
store.activeTab = 'albums';

// Scroll-aware navigation stack (save/restore across push/back).
const audioLayoutRef = ref(null);
const layoutScrollRef = computed(() => audioLayoutRef.value?.scrollElement ?? null);
const { currentView, currentParams, canGoBack, push, back, reset, pendingScrollRestore } =
  useNavigationStack('home', { scrollElRef: layoutScrollRef });

// Search and Liked Songs are the two views scoped ON the selected space rather
// than on what is browsable: they name it by library_id, which the backend
// honours as given (that is what keeps the selection alive on a space that has
// just gone). Every other view is filtered against the mounted spaces backend-
// side, so these two are the only ones that would go on offering tracks whose
// stream Navidrome answers with a JSON error mpv skips without a word. They fold
// back to home, where the "storage disconnected" message says why. Watching the
// view too, not just the flag: back() out of an album reached from a search is a
// second way into a stale search.
const scopedViews = ['search', 'liked'];
watch([() => store.disconnectedStorage, currentView], ([gone, view]) => {
  if (gone && scopedViews.includes(view)) reset();
});

// The pane follows the queue, and the queue survives an idle auto-stop: the
// backend publishes the saved session a play press would reopen. An explicit
// stop and a queue played out publish nothing to resume, so the player goes
// with them. The store's sticky displayTrack was a copy of that fact for the
// length of a fade.
const {
  isPlaying, isBuffering, shouldShowPlayer,
  displayed: nowPlaying, onAfterHide,
} = useSourcePlaybackVisibility('music_library', {
  content: () => store.nowPlaying,
});

// Live position with local interpolation (ms).
const { duration: durationMs, currentPosition: positionMs, progressPercentage: livePercent, seekTo } =
  useSourceProgress('music_library');

// === Player display ===
const playerTitle = computed(() => nowPlaying.value?.title || '');
const playerArtist = computed(() => nowPlaying.value?.artist || '');
const playerArtwork = computed(() => nowPlaying.value?.albumArtUrl || null);

// === Header title per view ===
const currentTitle = computed(() => {
  switch (currentView.value) {
    case 'album': return t('musicLibrary.albumDetails');
    case 'artist': return t('musicLibrary.artistDetails');
    case 'genre': return currentParams.value.genreLabel || t('musicLibrary.genre');
    case 'playlist': return t('musicLibrary.playlistDetails');
    case 'liked': return t('musicLibrary.playlists.likedSongs');
    case 'search': return t('musicLibrary.search');
    case 'queue': return t('musicLibrary.queue');
    default: return t('audioSources.musicLibrary');
  }
});

const detailsTitleView = computed(() =>
  ['album', 'artist', 'playlist'].includes(currentView.value)
);

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
function openLikedSongs() {
  push('liked');
}
// From the docked player's artwork/artist-line clicks — only the currently
// displayed track's ids are known here (no album/artist track counts).
function openPlayerAlbum() {
  const albumId = nowPlaying.value?.albumId;
  if (albumId) openAlbum({ id: albumId, name: nowPlaying.value.album });
}
function openPlayerArtist() {
  const artistId = nowPlaying.value?.artistId;
  if (artistId) openArtist({ id: artistId, name: nowPlaying.value.artist });
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
// A loading track is paused like a playing one: the press is about what the
// user hears next, and the backend takes `pause` in both phases.
function togglePlayPause() {
  const running = store.phase === 'playing' || store.phase === 'loading';
  if (running && store.canSend('pause')) store.pause();
  else if (!running && store.canSend('resume')) store.resume();
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

/* The .ml-controls / .ml-transport-main layout lives in AudioPlayer.vue, in
   :deep() — this row is slotted into it, and the same row is re-authored by the
   gallery's SourceStage, which scoped CSS here could never reach. */
</style>
