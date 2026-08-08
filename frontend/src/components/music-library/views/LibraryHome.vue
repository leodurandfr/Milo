<template>
  <div class="library-home">
    <!-- Storage spaces — only worth a row when there is a choice to make: with
         one storage space every tab below already shows all of it, and with the
         spaces merged there is nothing to pick. -->
    <ButtonGroup v-if="storageOptions.length > 1" v-model="store.activeLibraryId"
      :options="storageOptions" size="small" mobile-layout="scroll" />

    <!-- Top-level tabs -->
    <ButtonGroup v-model="store.activeTab" :options="tabOptions" mobile-layout="scroll"
      inactive-variant="background-neutral" />

    <!-- The storage space on screen has just been disconnected. It keeps its
         button until the user picks another one — dropping it here would swap
         the view out with no explanation, where holding it lets us say why it
         is empty. -->
    <MessageContent v-if="store.disconnectedStorage" :title="disconnectedTitle"
      :subtitle="t('musicLibrary.storage.disconnectedHint')" />

    <!-- Tab switch: same overlapping fade-slide crossfade as AudioSourceLayout's view
         transition — leaving + entering tab-content share one grid cell (.tab-transition),
         no out-in gap — so switching tabs feels as fast as switching views. -->
    <div v-else class="tab-transition"><Transition name="fade-slide">
      <div :key="store.activeTab" class="tab-content">
        <!-- ALBUMS -->
        <template v-if="store.activeTab === 'albums'">
          <div class="transition-container">
            <Transition name="content-swap">
              <div v-if="!store.albums.length && (store.albumsLoading || !store.albumsLoaded)" key="loading"
                class="albums-grid">
                <SkeletonAlbumCard v-for="i in 12" :key="`skeleton-${i}`" />
              </div>
              <MessageContent v-else-if="!store.albums.length" key="empty"
                v-bind="emptyState('musicLibrary.emptyLibrary', 'musicLibrary.emptyLibraryHint')" />
              <div v-else key="loaded">
                <div class="albums-grid">
                  <AlbumCard v-for="album in store.albums" :key="album.id" :album="album"
                    @click="$emit('select-album', album)" />
                </div>
                <div ref="sentinelRef" class="scroll-sentinel"></div>
              </div>
            </Transition>
          </div>
        </template>

        <!-- ARTISTS -->
        <template v-else-if="store.activeTab === 'artists'">
          <div class="transition-container">
            <Transition name="content-swap">
              <div v-if="!store.displayedArtistIndex.length && (store.artistsLoading || !store.artistsLoaded)"
                key="loading" class="rows-list">
                <SkeletonMediaRow v-for="i in 10" :key="`skeleton-${i}`" rounded-cover />
              </div>
              <MessageContent v-else-if="!store.displayedArtistIndex.length" key="empty"
                v-bind="emptyState('musicLibrary.noArtists')" />
              <div v-else key="loaded">
                <div class="index-list">
                  <div v-for="bucket in store.displayedArtistIndex" :key="bucket.name" class="index-bucket">
                    <p class="index-label text-mono">{{ bucket.name }}</p>
                    <MediaRow v-for="artist in bucket.artist" :key="artist.id" :cover-id="artist.coverArt"
                      :title="artist.name" :subtitle="t('musicLibrary.albumsCount', { count: artist.albumCount || 0 })"
                      rounded-cover @click="$emit('select-artist', artist)" />
                  </div>
                </div>
                <div ref="artistsSentinelRef" class="scroll-sentinel"></div>
              </div>
            </Transition>
          </div>
        </template>

        <!-- GENRES -->
        <template v-else-if="store.activeTab === 'genres'">
          <div class="transition-container">
            <Transition name="content-swap">
              <div v-if="!store.genres.length && (store.genresLoading || !store.genresLoaded)" key="loading"
                class="rows-list">
                <SkeletonGenreRow v-for="i in 10" :key="`skeleton-${i}`" />
              </div>
              <MessageContent v-else-if="!store.genres.length" key="empty"
                v-bind="emptyState('musicLibrary.noGenres')" />
              <div v-else key="loaded" class="rows-list">
                <div v-for="genre in store.genres" :key="genre.value" v-press class="genre-row"
                  @click="$emit('select-genre', genre)">
                  <span class="genre-name heading-3">{{ genre.value }}</span>
                  <span class="genre-count text-mono">{{ t('musicLibrary.songsCount', { count: genre.songCount || 0 })
                  }}</span>
                </div>
              </div>
            </Transition>
          </div>
        </template>

        <!-- PLAYLISTS -->
        <template v-else>
          <div class="playlists-actions">
            <Button variant="background-strong" size="small" left-icon="plus" @click="createOpen = true">
              {{ t('musicLibrary.playlists.newPlaylist') }}
            </Button>
          </div>

          <MediaRow icon="heart" :title="t('musicLibrary.playlists.likedSongs')"
            :subtitle="t('musicLibrary.songsCount', { count: store.likedSongsCount })"
            @click="$emit('select-liked')" />

          <div class="transition-container">
            <Transition name="content-swap">
              <div v-if="!store.playlists.length && (store.playlistsLoading || !store.playlistsLoaded)" key="loading"
                class="rows-list">
                <SkeletonMediaRow v-for="i in 10" :key="`skeleton-${i}`" />
              </div>
              <MessageContent v-else-if="!store.playlists.length" key="empty"
                v-bind="emptyState('musicLibrary.noPlaylists')" />
              <div v-else key="loaded" class="rows-list">
                <MediaRow v-for="playlist in store.playlists" :key="playlist.id" :cover-id="playlist.coverArt"
                  :title="playlist.name" :subtitle="t('musicLibrary.songsCount', { count: playlist.songCount || 0 })"
                  @click="$emit('select-playlist', playlist)" />
              </div>
            </Transition>
          </div>
        </template>
      </div>
    </Transition></div>

    <PlaylistNameModal :is-open="createOpen" :title="t('musicLibrary.playlists.createTitle')"
      :submit-label="t('musicLibrary.playlists.create')" :loading="creating" @close="createOpen = false"
      @submit="handleCreate" />
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import { useInfiniteScroll } from '@/composables/useInfiniteScroll';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import Button from '@/components/ui/Button.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import AlbumCard from '../cards/AlbumCard.vue';
import MediaRow from '../cards/MediaRow.vue';
import SkeletonAlbumCard from '../cards/SkeletonAlbumCard.vue';
import SkeletonMediaRow from '../cards/SkeletonMediaRow.vue';
import SkeletonGenreRow from '../cards/SkeletonGenreRow.vue';
import PlaylistNameModal from '../PlaylistNameModal.vue';

defineEmits(['select-album', 'select-artist', 'select-genre', 'select-playlist', 'select-liked']);

const { t } = useI18n();
const store = useMusicLibraryStore();

// New-playlist creation (empty playlist; tracks are added later via the ⋯ menu).
const createOpen = ref(false);
const creating = ref(false);

async function handleCreate(name) {
  if (creating.value) return;
  creating.value = true;
  const created = await store.createPlaylist(name);
  creating.value = false;
  if (created) createOpen.value = false;
}

// Building-state subtitle: the reassuring hint until this storage space has
// tracks, then live progress ("1,234 tracks indexed…"). Shared by every tab's
// empty state during a scan. The count is the space's own — Navidrome's global
// one does not move until the scan ends.
const buildingSubtitle = computed(() =>
  store.activeStorageTrackCount > 0
    ? t('musicLibrary.buildingProgress', { count: store.activeStorageTrackCount })
    : t('musicLibrary.buildingHint')
);

// A tab with nothing in it has three possible reasons, and they are NOT
// interchangeable — the middle one used to be rendered as the last one, which
// told a user whose NAS was mounted and full to go connect a NAS:
//   1. a scan is filling it        → wait, here is the progress
//   2. the index lost its files    → the music is there, re-index it
//   3. there is genuinely nothing  → connect some storage
// Only (2) is actionable, and it is the only one a person can't diagnose alone.
const rescanning = ref(false);

async function handleRescan() {
  if (rescanning.value) return;
  rescanning.value = true;
  await store.rescan();
  rescanning.value = false;
}

function emptyState(titleKey, subtitleKey) {
  if (store.isScanning) {
    return {
      loading: true,
      title: t('musicLibrary.building'),
      subtitle: buildingSubtitle.value,
    };
  }
  if (store.unindexedStorage) {
    return {
      title: t('musicLibrary.storage.notIndexed', { name: store.unindexedStorage.name }),
      subtitle: t('musicLibrary.storage.notIndexedHint'),
      ctaLabel: t('musicLibrary.storage.reindex'),
      ctaLoading: rescanning.value,
      ctaClick: handleRescan,
    };
  }
  return { title: t(titleKey), subtitle: subtitleKey ? t(subtitleKey) : '' };
}

const disconnectedTitle = computed(() =>
  store.disconnectedStorage?.kind === 'usb'
    ? t('musicLibrary.storage.usbDisconnected')
    : t('musicLibrary.storage.shareDisconnected', {
      name: store.disconnectedStorage?.name || '',
    })
);

// One button per browsable storage space, labelled with the name the user gave
// it (a USB key) or the share's name. Nothing to pick when the spaces are
// merged. The space that has just been disconnected keeps its button while it
// is the selected one, so the message above has something to belong to.
const storageOptions = computed(() => {
  if (!store.separateStorages) return [];
  const options = store.browsableStorages.map((storage) => ({
    label: storage.name,
    value: storage.library_id,
  }));
  const gone = store.disconnectedStorage;
  if (gone) options.push({ label: gone.name, value: gone.library_id });
  return options;
});

const tabOptions = computed(() => [
  { label: t('musicLibrary.tabs.albums'), value: 'albums' },
  { label: t('musicLibrary.tabs.artists'), value: 'artists' },
  { label: t('musicLibrary.tabs.genres'), value: 'genres' },
  { label: t('musicLibrary.tabs.playlists'), value: 'playlists' },
]);

// Load the active tab's data lazily; each loader is idempotent (guarded by its
// own loaded flag).
function loadTab(tab) {
  if (tab === 'albums') store.loadAlbums();
  else if (tab === 'artists') store.loadArtists();
  else if (tab === 'genres') store.loadGenres();
  else {
    store.loadPlaylists();
    store.loadLikedSongs();
  }
}

watch(() => store.activeTab, loadTab);

// Infinite scroll for the albums grid.
const { sentinelRef } = useInfiniteScroll({
  onLoadMore: () => store.loadMoreAlbums(),
  canLoadMore: computed(() => store.activeTab === 'albums' && store.albumsHasMore),
  isLoading: computed(() => store.albumsLoading),
});

// Same sentinel for the artists index — but nothing is fetched here (the whole
// index arrived in one call), it only widens the store's render window, so
// there is no loading flag to gate on.
const { sentinelRef: artistsSentinelRef } = useInfiniteScroll({
  onLoadMore: () => store.renderMoreArtists(),
  canLoadMore: computed(() => store.activeTab === 'artists' && store.artistsHasMore),
});

onMounted(async () => {
  // Storage spaces before the first catalog call: every one of them is scoped
  // to a library, so loading a tab first would fetch the wrong (unscoped) list
  // and immediately throw it away when the selection lands. The same response
  // carries the scan flag that backs the "building library…" empty state; every
  // later change arrives on the storages_changed push.
  await store.loadStorages();
  loadTab(store.activeTab);
});
</script>

<style scoped>
.library-home {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

/* Holds each tab's stacked content (grid / lists / actions) and is the element
   the fade-slide transition swaps on tab change. */
.tab-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
  width: 100%;
}

/* Stacks each tab's skeleton / empty / loaded states in one cell so content-swap
   crossfades them (no layout jump) when a tab's data lands. */
.transition-container {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
}

.transition-container > * {
  grid-row: 1;
  grid-column: 1;
  align-self: start;
}

/* Overlapping tab-switch crossfade (matches AudioSourceLayout's view transition):
   both tab-contents occupy one grid cell so there's no out-in blank beat. */
.tab-transition {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  min-height: 0;
}

:deep(.fade-slide-enter-active),
:deep(.fade-slide-leave-active) {
  grid-row: 1;
  grid-column: 1;
  align-self: start;
}

:deep(.fade-slide-enter-active) {
  transition-delay: 100ms;
}

.albums-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  row-gap: var(--space-05);
  column-gap: var(--space-04);
}

.scroll-sentinel {
  height: 1px;
}

.index-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.index-bucket {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.index-label {
  margin: 0;
  color: var(--color-text-secondary);
  padding-left: var(--space-02);
}

.rows-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.playlists-actions {
  display: flex;
  flex-direction: row;
  justify-content: flex-start;
}

.genre-row {
  display: flex;
  flex-direction: row;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-03);
  padding: var(--space-03) var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-background-neutral-50);
  cursor: pointer;
  min-width: 0;
}

.genre-name {
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.genre-count {
  flex-shrink: 0;
  color: var(--color-text-secondary);
}

@media (max-aspect-ratio: 4/3) {
  .albums-grid {
    grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
    column-gap: var(--space-03);
  }
}
</style>
