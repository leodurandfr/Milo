<template>
  <div class="library-home">
    <!-- Top-level tabs -->
    <ButtonGroup v-model="store.activeTab" :options="tabOptions" mobile-layout="wrap" />

    <!-- ALBUMS -->
    <template v-if="store.activeTab === 'albums'">
      <MessageContent v-if="store.albumsLoading && !store.albums.length" loading :title="t('musicLibrary.loading')" />
      <MessageContent
        v-else-if="!store.albums.length"
        :loading="store.isScanning"
        :title="store.isScanning ? t('musicLibrary.building') : t('musicLibrary.emptyLibrary')"
        :subtitle="store.isScanning ? t('musicLibrary.buildingHint') : t('musicLibrary.emptyLibraryHint')"
      />
      <template v-else>
        <div class="albums-grid">
          <AlbumCard
            v-for="album in store.albums"
            :key="album.id"
            :album="album"
            @click="$emit('select-album', album)"
          />
        </div>
        <div ref="sentinelRef" class="scroll-sentinel"></div>
      </template>
    </template>

    <!-- ARTISTS -->
    <template v-else-if="store.activeTab === 'artists'">
      <MessageContent v-if="store.artistsLoading && !store.artistIndex.length" loading :title="t('musicLibrary.loading')" />
      <MessageContent v-else-if="!store.artistIndex.length" :title="t('musicLibrary.noArtists')" />
      <div v-else class="index-list">
        <div v-for="bucket in store.artistIndex" :key="bucket.name" class="index-bucket">
          <p class="index-label text-mono">{{ bucket.name }}</p>
          <MediaRow
            v-for="artist in bucket.artist"
            :key="artist.id"
            :cover-id="artist.coverArt"
            :title="artist.name"
            :subtitle="t('musicLibrary.albumsCount', { count: artist.albumCount || 0 })"
            rounded-cover
            @click="$emit('select-artist', artist)"
          />
        </div>
      </div>
    </template>

    <!-- GENRES -->
    <template v-else-if="store.activeTab === 'genres'">
      <MessageContent v-if="store.genresLoading && !store.genres.length" loading :title="t('musicLibrary.loading')" />
      <MessageContent v-else-if="!store.genres.length" :title="t('musicLibrary.noGenres')" />
      <div v-else class="rows-list">
        <div
          v-for="genre in store.genres"
          :key="genre.value"
          v-press
          class="genre-row"
          @click="$emit('select-genre', genre)"
        >
          <span class="genre-name heading-3">{{ genre.value }}</span>
          <span class="genre-count text-mono">{{ t('musicLibrary.songsCount', { count: genre.songCount || 0 }) }}</span>
        </div>
      </div>
    </template>

    <!-- PLAYLISTS -->
    <template v-else>
      <div class="playlists-actions">
        <Button variant="background-strong" size="small" left-icon="plus" @click="createOpen = true">
          {{ t('musicLibrary.playlists.newPlaylist') }}
        </Button>
      </div>
      <MessageContent v-if="store.playlistsLoading && !store.playlists.length" loading :title="t('musicLibrary.loading')" />
      <MessageContent v-else-if="!store.playlists.length" :title="t('musicLibrary.noPlaylists')" />
      <div v-else class="rows-list">
        <MediaRow
          v-for="playlist in store.playlists"
          :key="playlist.id"
          :cover-id="playlist.coverArt"
          :title="playlist.name"
          :subtitle="t('musicLibrary.songsCount', { count: playlist.songCount || 0 })"
          @click="$emit('select-playlist', playlist)"
        />
      </div>
    </template>

    <PlaylistNameModal
      :is-open="createOpen"
      :title="t('musicLibrary.playlists.createTitle')"
      :submit-label="t('musicLibrary.playlists.create')"
      :loading="creating"
      @close="createOpen = false"
      @submit="handleCreate"
    />
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
import PlaylistNameModal from '../PlaylistNameModal.vue';

defineEmits(['select-album', 'select-artist', 'select-genre', 'select-playlist']);

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
  else store.loadPlaylists();
}

watch(() => store.activeTab, loadTab);

// Infinite scroll for the albums grid.
const { sentinelRef } = useInfiniteScroll({
  onLoadMore: () => store.loadMoreAlbums(),
  canLoadMore: computed(() => store.activeTab === 'albums' && store.albumsHasMore),
  isLoading: computed(() => store.albumsLoading),
});

onMounted(() => {
  // Scan status backs the albums empty state ("building library…").
  store.refreshScanStatus();
  loadTab(store.activeTab);
});
</script>

<style scoped>
.library-home {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.albums-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: var(--space-04);
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
</style>
