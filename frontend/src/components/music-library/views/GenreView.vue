<template>
  <div class="genre-view">
    <ButtonGroup
      v-model="viewMode"
      :options="[
        { label: t('musicLibrary.sections.tracks'), value: 'tracks' },
        { label: t('musicLibrary.sections.albums'), value: 'albums' },
      ]"
      mobile-layout="scroll"
      inactive-variant="background-neutral"
    />

    <div class="tab-transition"><Transition name="fade-slide">
      <div :key="viewMode" class="tab-content">
        <template v-if="viewMode === 'tracks'">
          <div class="transition-container">
            <Transition name="content-swap">
              <MessageContent v-if="loading && !songs.length" key="loading" loading :title="t('musicLibrary.loading')" />
              <MessageContent v-else-if="!songs.length" key="notfound" :title="t('musicLibrary.noTracks')" />
              <div v-else key="loaded" class="content-stack">
                <div class="genre-actions">
                  <div class="genre-buttons">
                    <Button variant="brand" left-icon="play" @click="playFrom(0)">{{ t('musicLibrary.play') }}</Button>
                    <IconButton icon="shuffle" variant="on-dark" :aria-label="t('musicLibrary.shuffle')" @click="shufflePlay" />
                  </div>
                  <p class="genre-count text-mono">{{ t('musicLibrary.tracksCount', { count: songs.length }) }}</p>
                </div>

                <div class="tracks">
                  <TrackRow
                    v-for="(song, idx) in songs"
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
                </div>
              </div>
            </Transition>
          </div>
        </template>

        <template v-else>
          <div class="transition-container">
            <Transition name="content-swap">
              <MessageContent v-if="loading && !albums.length" key="loading" loading :title="t('musicLibrary.loading')" />
              <MessageContent v-else-if="!albums.length" key="notfound" :title="t('musicLibrary.noTracks')" />
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
import { ref, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import MessageContent from '@/components/ui/MessageContent.vue';
import Button from '@/components/ui/Button.vue';
import IconButton from '@/components/ui/IconButton.vue';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import TrackRow from '@/components/audio/TrackRow.vue';
import AlbumCard from '../cards/AlbumCard.vue';

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
const songs = ref([]);
const albums = ref([]);
const loading = ref(false);

function playFrom(index) {
  store.playContext(songs.value, index, false);
}

function shufflePlay() {
  if (!songs.value.length) return;
  const start = Math.floor(Math.random() * songs.value.length);
  store.playContext(songs.value, start, true);
}

watch(() => props.genre, async (genre) => {
  loading.value = true;
  [songs.value, albums.value] = await Promise.all([
    store.fetchGenreSongs(genre),
    store.fetchGenreAlbums(genre),
  ]);
  loading.value = false;
}, { immediate: true });
</script>

<style scoped>
.genre-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.tab-transition {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  min-height: 0;
}

.tab-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
  width: 100%;
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

.transition-container {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
}

.transition-container > * {
  grid-row: 1;
  grid-column: 1;
  align-self: start;
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

.tracks {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.albums-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  row-gap: var(--space-05);
  column-gap: var(--space-04);
}

@media (max-aspect-ratio: 4/3) {
  .albums-grid {
    grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
    column-gap: var(--space-03);
  }
}
</style>
