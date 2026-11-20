<template>
  <div class="genre-view">
    <ButtonGroup
      v-model="viewMode"
      :options="[
        { label: t('musicLibrary.sections.tracks'), value: 'tracks' },
        { label: t('musicLibrary.sections.albums'), value: 'albums' },
      ]"
      mobile-layout="scroll"
    />

    <div class="swap-stack"><Transition name="fade-slide">
      <div :key="viewMode" class="tab-content">
        <template v-if="viewMode === 'tracks'">
          <div class="swap-stack">
            <Transition name="fade-slide">
              <MessageContent v-if="songsError" key="unavailable" icon="musicNote"
                :title="t('musicLibrary.catalogUnreachable')" :cta-label="t('musicLibrary.retry')"
                cta-variant="control" :cta-loading="songsLoading" :cta-click="reloadSongs" />
              <MessageContent v-else-if="!songsRead" key="loading" loading />
              <MessageContent v-else-if="!songs.length" key="empty" icon="musicNote" :title="t('musicLibrary.noTracks')" />
              <div v-else key="loaded" class="content-stack">
                <div class="genre-actions">
                  <div class="genre-buttons">
                    <Button variant="brand" left-icon="play" @click="playFrom(0)">{{ t('musicLibrary.play') }}</Button>
                    <IconButton icon="shuffle" variant="control" :aria-label="t('musicLibrary.shuffle')" @click="shufflePlay" />
                  </div>
                  <p class="genre-count text-mono-medium">{{ t('musicLibrary.tracksCount', { count: songs.length }) }}</p>
                </div>

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
        </template>

        <template v-else>
          <div class="swap-stack">
            <Transition name="fade-slide">
              <MessageContent v-if="albumsError" key="unavailable" icon="musicNote"
                :title="t('musicLibrary.catalogUnreachable')" :cta-label="t('musicLibrary.retry')"
                cta-variant="control" :cta-loading="albumsLoading" :cta-click="reloadAlbums" />
              <MessageContent v-else-if="!albumsRead" key="loading" loading />
              <MessageContent v-else-if="!albums.length" key="empty" icon="musicNote" :title="t('musicLibrary.noAlbums')" />
              <div v-else key="loaded" class="albums-grid">
                <AlbumCard v-for="album in albums" :key="album.id" :album="album" @click="$emit('select-album', album)" />
              </div>
            </Transition>
          </div>
        </template>
      </div>
    </Transition></div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import MessageContent from '@/components/ui/MessageContent.vue';
import Button from '@/components/ui/Button.vue';
import IconButton from '@/components/ui/IconButton.vue';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import TrackRow from '@/components/audio/TrackRow.vue';
import TrackList from '@/components/audio/TrackList.vue';
import LibraryItemMenu from '../LibraryItemMenu.vue';
import AlbumCard from '../cards/AlbumCard.vue';
import { useRenderWindow } from '@/composables/useRenderWindow';
import { useCatalogRead } from '@/composables/useCatalogRead';

const props = defineProps({
  genre: {
    type: String,
    required: true,
  },
});

defineEmits(['select-album']);

const { t } = useI18n();
const store = useMusicLibraryStore();

const viewMode = ref('tracks');

// The two tabs are two reads, and one can fail while the other lands: each
// says so on its own tab. A genre does not "not exist" — a 404 here is a
// failure like any other, so the only error a tab tells apart is the read's.
const {
  value: songsRead, error: songsError, loading: songsLoading, reload: reloadSongs,
} = useCatalogRead(() => props.genre, store.fetchGenreSongs);
const {
  value: albumsRead, error: albumsError, loading: albumsLoading, reload: reloadAlbums,
} = useCatalogRead(() => props.genre, store.fetchGenreAlbums);

// The whole genre, every page of it: the count, Play and Shuffle all read it.
const songs = computed(() => songsRead.value || []);
const albums = computed(() => albumsRead.value || []);
const { visible: visibleSongs, hasMore, sentinelRef } = useRenderWindow(songs);

function playFrom(index) {
  store.playContext(songs.value, index, false);
}

function shufflePlay() {
  store.playShuffled(songs.value);
}
</script>

<style scoped>
.genre-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.tab-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
  width: 100%;
}

.content-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.genre-actions {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-03);
  flex-wrap: wrap;
}

.genre-count {
  margin: 0;
  color: var(--color-text-secondary);
}

.genre-buttons {
  display: flex;
  flex-direction: row;
  gap: var(--space-02);
}


/* Same column count and column gap as the radio favorites grid, so an album
   cover and a station logo are the same size on the same screen. Rows keep the
   wider gap: the card carries two lines of text under the cover. */
.albums-grid {
  display: grid;
  grid-template-columns: repeat(var(--card-grid-columns), minmax(0, 1fr));
  row-gap: var(--space-05);
  column-gap: var(--space-03);
}

</style>
