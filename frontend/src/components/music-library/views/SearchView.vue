<template>
  <div class="search-view">
    <InputText
      v-model="store.searchTerm"
      :placeholder="t('musicLibrary.searchPlaceholder')"
      variant="background-neutral"
      icon="search"
      @update:modelValue="onInput"
    />

    <div class="transition-container">
      <Transition name="content-swap">
        <MessageContent v-if="store.searchLoading" key="loading" loading :loading-delay="0" :title="t('musicLibrary.loading')" />

        <div v-else-if="store.hasSearched && !store.searchEmpty" key="results" class="content-stack">
          <!-- Artists -->
          <section v-if="results.artists.length" class="section">
            <h2 class="heading-2 section-title">{{ t('musicLibrary.sections.artists') }}</h2>
            <div class="rows-list">
              <MediaRow
                v-for="artist in results.artists"
                :key="artist.id"
                :cover-id="artist.coverArt"
                :title="artist.name"
                :subtitle="t('musicLibrary.albumsCount', { count: artist.albumCount || 0 })"
                rounded-cover
                @click="$emit('select-artist', artist)"
              />
            </div>
          </section>

          <!-- Albums -->
          <section v-if="results.albums.length" class="section">
            <h2 class="heading-2 section-title">{{ t('musicLibrary.sections.albums') }}</h2>
            <div class="rows-list">
              <MediaRow
                v-for="album in results.albums"
                :key="album.id"
                :cover-id="album.coverArt"
                :title="album.name"
                :subtitle="album.artist || ''"
                @click="$emit('select-album', album)"
              />
            </div>
          </section>

          <!-- Tracks -->
          <section v-if="results.songs.length" class="section">
            <h2 class="heading-2 section-title">{{ t('musicLibrary.sections.tracks') }}</h2>
            <div class="tracks">
              <TrackRow
                v-for="(song, idx) in results.songs"
                :key="song.id"
                :song="song"
                :number="idx + 1"
                :current="song.id === store.currentTrackId"
                :playing="store.isPlaying"
                show-artist
                show-menu
                @play="playSong(idx)"
                @menu="store.requestAddToPlaylist([song.id])"
              />
            </div>
          </section>
        </div>

        <MessageContent
          v-else-if="store.hasSearched"
          key="empty"
          icon="search"
          :title="t('musicLibrary.noResultsFor', { query: store.lastSearchTerm })"
        />

        <MessageContent v-else key="prompt" icon="search" :title="t('musicLibrary.searchPrompt')" />
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import { useDebounce } from '@/composables/useDebounce';
import InputText from '@/components/ui/InputText.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import MediaRow from '../cards/MediaRow.vue';
import TrackRow from '@/components/audio/TrackRow.vue';

defineEmits(['select-album', 'select-artist']);

const { t } = useI18n();
const store = useMusicLibraryStore();

const results = computed(() => store.searchResults);

const { debounced: debouncedSearch } = useDebounce(() => store.search());

function onInput() {
  if (!store.searchTerm.trim()) {
    store.clearSearch();
    return;
  }
  debouncedSearch();
}

function playSong(index) {
  store.playContext(results.value.songs, index, false);
}
</script>

<style scoped>
.search-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
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

.section {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.section-title {
  margin: 0;
  color: var(--color-text);
}

.rows-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.tracks {
  display: flex;
  flex-direction: column;
  gap: 0;
}
</style>
