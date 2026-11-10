<!-- frontend/src/components/spotify/views/SpotifySearchView.vue -->
<!-- Spotify's catalog searched as it is typed: its songs, artists, albums and
     playlists, and the queries Spotify completes the term into while it is
     typed — on the kiosk's keyboard, above its display; elsewhere floating over
     the results. A song plays on its own, as in Spotify's apps; a card opens
     its page, and a shelf with more than its row shows opens whole. -->
<template>
  <div class="spotify-search">
    <div class="search-field">
      <InputText v-model="store.searchTerm" :placeholder="t('spotify.searchPlaceholder')" icon="search"
        :maxlength="MAX_QUERY" :suggestions="store.completions"
        @update:model-value="onInput" @submit="searchNow" @blur="typing = false" />

      <!-- Over the results, only while the term is being typed and no virtual
           keyboard draws them: a submit, a pick or the blur puts it away.
           Picked on click, so the click never lands on what lies under the
           list once it is gone (a section's "show all"); the press keeps the
           field's focus (mousedown.prevent), so the list stays until then. -->
      <Transition name="completions">
        <div v-if="typing && store.completions.length && !keyboardOpen" class="completions glass-menu">
          <button v-for="text in store.completions" :key="text" v-press type="button"
            class="completion text-body-medium" @mousedown.prevent @click="complete(text)">
            <SvgIcon name="search" :size="20" class="completion-icon" />
            <span class="completion-text">{{ text }}</span>
          </button>
        </div>
      </Transition>
    </div>

    <div class="swap-stack">
      <Transition name="fade-slide">
        <!-- The search asks again by itself once the account is signed in;
             a session that never comes back the same (a transfer) has the
             button. -->
        <MessageContent v-if="store.searchError === 'not_signed_in'" key="signing-in" loading
          :title="t('spotify.signingIn')" :cta-label="t('spotify.retry')" cta-variant="control"
          :cta-click="retry" />

        <MessageContent v-else-if="store.searchError" :key="`error-${store.searchError}`" icon="search"
          :title="errorTitle"
          :cta-label="store.searchError === 'unavailable' ? '' : t('spotify.retry')" cta-variant="control"
          :cta-click="retry" />

        <MessageContent v-else-if="store.searchLoading && !store.hasSearched" key="loading" loading
          :loading-delay="0" />

        <div v-else-if="store.hasSearched && !store.searchEmpty" key="results" class="sections">
          <section v-if="results.tracks.length" class="section">
            <SpotifySectionTitle :title="t('spotify.searchSongs')" />
            <TrackList>
              <TrackRow
                v-for="(track, idx) in results.tracks"
                :key="track.uri"
                :song="rowSong(track)"
                :number="idx + 1"
                :current="track.uri === store.currentTrackUri"
                :playing="store.isPlaying"
                show-artist
                show-cover
                :cover-url="track.thumbnail || ''"
                @play="store.playContext(track.uri)"
              >
                <template #menu>
                  <SpotifyTrackMenu :track="track" kind="search"
                    @artist="$emit('select-artist', $event)"
                    @album="$emit('select-album', track.album)"
                    @radio="$emit('select-radio', $event)" />
                </template>
              </TrackRow>
            </TrackList>
          </section>

          <section v-for="shelf in shelves" :key="shelf.id" class="section">
            <SpotifySectionTitle :title="shelf.title" :linked="shelf.items.length >= columns"
              @open="$emit('show-section', shelf)" />
            <SpotifyShelfRow :items="shelf.items" @select="$emit('select', $event)" />
          </section>
        </div>

        <MessageContent v-else-if="store.hasSearched" key="empty" icon="search"
          :title="t('spotify.noResultsFor', { query: store.lastSearchTerm })" />

        <MessageContent v-else key="prompt" icon="search" :title="t('spotify.searchPrompt')" />
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSpotifyStore } from '@/stores/spotifyStore';
import { useDebounce } from '@/composables/useDebounce';
import { useCardGridColumns } from '@/composables/useCardGridColumns';
import { useVirtualKeyboard } from '@/composables/useVirtualKeyboard';
import InputText from '@/components/ui/InputText.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import TrackList from '@/components/audio/TrackList.vue';
import TrackRow from '@/components/audio/TrackRow.vue';
import SpotifySectionTitle from '../SpotifySectionTitle.vue';
import SpotifyShelfRow from '../SpotifyShelfRow.vue';
import SpotifyTrackMenu from '../SpotifyTrackMenu.vue';

// `select`: a card, with the kind of page it opens; `show-section`: a shelf to
// open whole; the rest: what a song's ⋯ menu leads to.
defineEmits(['select', 'show-section', 'select-artist', 'select-album', 'select-radio']);

// What Spotify searches at most (go-librespot refuses longer).
const MAX_QUERY = 200;
// The completions follow the typing closely; the results, once it pauses.
const SUGGEST_DELAY_MS = 150;
const SEARCH_DELAY_MS = 400;

const { t } = useI18n();
const store = useSpotifyStore();
const { columns } = useCardGridColumns();
const keyboard = useVirtualKeyboard();

// The term is being typed: the completions float over the results, unless
// the virtual keyboard is open, which draws them itself.
const typing = ref(false);
const keyboardOpen = computed(() => keyboard.isVisible.value);

const results = computed(() => store.searchResults);

const shelves = computed(() => [
  { id: 'search:artists', title: t('spotify.searchArtists'), items: results.value.artists },
  { id: 'search:albums', title: t('spotify.searchAlbums'), items: results.value.albums },
  { id: 'search:playlists', title: t('spotify.searchPlaylists'), items: results.value.playlists },
].filter((shelf) => shelf.items.length > 0));

const ERROR_TITLES = {
  unavailable: 'spotify.searchUnavailable',
  rate_limited: 'spotify.searchRateLimited',
  failed: 'spotify.searchFailed',
};
const errorTitle = computed(() => t(ERROR_TITLES[store.searchError] ?? 'spotify.searchFailed'));

const { debounced: debouncedSearch, cancel: cancelSearch } = useDebounce(() => store.search(), SEARCH_DELAY_MS);
const { debounced: debouncedSuggest, cancel: cancelSuggest } = useDebounce(() => store.suggest(), SUGGEST_DELAY_MS);

// Searching as it is typed stops once the daemon said it cannot search, or
// Spotify that it gets too many: every pause would ask again into the refusal
// (four of Spotify's operations each). Enter and Retry still ask.
const HELD_BACK = ['unavailable', 'rate_limited'];

function onInput() {
  if (!store.searchTerm.trim()) {
    cancelSearch();
    cancelSuggest();
    store.clearSearch();
    return;
  }
  if (HELD_BACK.includes(store.searchError)) return;
  typing.value = true;
  debouncedSuggest();
  debouncedSearch();
}

// Enter, or a pick: at once, without waiting for the pause.
function searchNow() {
  typing.value = false;
  cancelSuggest();
  store.dropCompletions();
  cancelSearch();
  store.search();
}

function retry() {
  cancelSearch();
  store.search({ force: true });
}

// A search that met the sign-in is asked again once it is done (a start, a
// profile switch): nothing else would, and the page kept would spin for good.
watch(() => (store.signingIn ? null : store.account), (account) => {
  if (account && store.searchError === 'not_signed_in' && store.searchTerm.trim()) retry();
});

// A completion picked from the floating list is the search. The field gives
// its focus up, which puts a phone's own keyboard away.
function complete(text) {
  store.searchTerm = text;
  document.activeElement?.blur();
  searchNow();
}

function rowSong(track) {
  return {
    title: track.title,
    artist: track.artists.map((a) => a.name).filter(Boolean).join(', '),
    duration: (track.duration_ms || 0) / 1000,
  };
}
</script>

<style scoped>
.spotify-search {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.search-field {
  position: relative;
  /* Over the results it floats on. */
  z-index: 2;
}

.completions {
  position: absolute;
  top: calc(100% + var(--space-02));
  left: 0;
  right: 0;
  padding: 10px var(--space-02);
  border-radius: var(--radius-05);
  transform-origin: top center;
}

.completion {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  width: 100%;
  min-height: 44px;
  padding: var(--space-02) var(--space-03);
  border: none;
  border-radius: var(--radius-03);
  background: transparent;
  color: var(--color-text);
  text-align: left;
  cursor: pointer;
}

.completion-icon {
  flex-shrink: 0;
  color: var(--color-text-secondary);
}

.completion-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.completions-enter-active {
  transition:
    opacity var(--transition-fast),
    transform var(--transition-spring-light);
}

.completions-leave-active {
  transition:
    opacity 150ms var(--easeInCubic),
    transform 150ms var(--easeInCubic);
}

.completions-enter-from,
.completions-leave-to {
  opacity: 0;
  transform: translateY(-6px) scale(0.98);
}

.sections {
  display: flex;
  flex-direction: column;
  gap: var(--space-07);
}

.section {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}
</style>
