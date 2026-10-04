<template>
  <div class="music-library-source">
    <!-- The navigation, and the full player it expands into: BrowserSourceViews
         swaps the two. -->
    <BrowserSourceViews source="music_library" :playback="playback"
      :can-open-album="!!nowPlaying?.albumId" :can-open-artist="!!nowPlaying?.artistId"
      @artwork-click="openPlayerAlbum" @secondary-click="openPlayerArtist">
      <template #navigation="{ bar }">
        <AudioSourceLayout ref="audioLayoutRef" :show-player="shouldShowPlayer"
          :header-title="currentTitle" :header-show-back="canGoBack" :header-title-muted="detailsTitleView"
          header-icon="music_library" gradient="music_library"
          :header-actions-key="currentView" :content-key="currentView"
          :player-mobile-height="144" :pending-scroll-restore="pendingScrollRestore"
          @header-back="goBack" @scroll-restored="onScrollRestored">

          <!-- Header actions (home only): queue + search. Search is scoped on the
               selected storage space, so it goes away with it (see scopedViews
               below); the queue is what is loaded, not what is browsable, and stays. -->
          <template v-if="currentView === 'home'" #header-actions>
            <IconButton icon="queue" @click="goToQueue" />
            <IconButton v-if="!store.disconnectedStorage" icon="search"
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

          <!-- Docked player: it reads what it draws from the state; the album
               and the artist it emits open here, in the navigation. -->
          <template #player>
            <AudioPlayer v-bind="bar" source="music_library"
              @artwork-click="openPlayerAlbum" @secondary-click="openPlayerArtist">
              <!-- The star over the cover's corner, opposite the expand button. -->
              <template #artwork-action>
                <IconButton :icon="store.currentStarred ? 'heart' : 'heartOff'" variant="on-image" size="small"
                  @click="store.toggleCurrentStar()" />
              </template>
            </AudioPlayer>
          </template>
        </AudioSourceLayout>
      </template>

      <!-- The star is not a command, so it is this source's to add; the album and
           the artist open in the navigation behind the player. -->
      <template #top-end>
        <IconButton :icon="store.currentStarred ? 'heart' : 'heartOff'" variant="control" size="medium"
          @click="store.toggleCurrentStar()" />
      </template>
    </BrowserSourceViews>

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
import { useI18n } from '@/services/i18n';
import IconButton from '@/components/ui/IconButton.vue';
import AudioPlayer from '@/components/audio/AudioPlayer.vue';
import BrowserSourceViews from '@/components/audio/BrowserSourceViews.vue';
import AudioSourceLayout from '@/components/audio/AudioSourceLayout.vue';

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
const playback = useSourcePlaybackVisibility('music_library', {
  content: () => store.nowPlaying,
});
const { shouldShowPlayer, displayed: nowPlaying } = playback;

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
</script>

<style scoped>
/* Fills the transition slot; BrowserSourceViews inside is width/height 100%. */
.music-library-source {
  width: 100%;
  height: 100%;
}

::-webkit-scrollbar {
  display: none;
}
</style>
