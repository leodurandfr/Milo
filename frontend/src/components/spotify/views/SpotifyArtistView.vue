<template>
  <div class="spotify-artist">
    <div class="swap-stack">
      <Transition name="fade-slide">
        <!-- Until the page and the first tracks are in: drawn one after the
             other, the tracks would push the sections down as they arrive. -->
        <MessageContent v-if="(!page && !pageError) || tracksPending" key="loading" loading />

        <!-- Neither the page nor a track: nothing to show but a retry. -->
        <MessageContent v-else-if="pageError && !tracks.length" key="error" icon="spotify"
          :title="errorTitle(pageError)" :cta-label="t('spotify.retry')" cta-variant="control" :cta-click="load" />

        <div v-else key="loaded" class="sections">
          <DetailHeader
            :image-src="headerImage"
            :fallback="musicPlaceholder"
            :title="headerTitle"
            :subtitle-meta="page?.listeners || ''"
            :show-play="tracks.length > 0"
            :show-shuffle="tracks.length > 0"
            @play="play()"
            @shuffle="shufflePlay"
          />

          <section v-if="popular.length" class="section">
            <h2 class="section-title heading-2">{{ page?.popular_title || t('spotify.popular') }}</h2>
            <!-- Five, the sixth fading under the button, then all ten. -->
            <ShowMoreClip :peek-index="POPULAR_FIRST" :has-more="canShowMore" :items="topTracks"
              @more="state.popularExpanded = true">
              <div class="tracks">
                <TrackRow
                  v-for="(track, idx) in popular"
                  :key="`${track.uri}-${idx}`"
                  :song="rowSong(track)"
                  :number="idx + 1"
                  :current="isCurrent(track)"
                  :playing="store.isPlaying"
                  show-artist
                  show-cover
                  :cover-url="track.thumbnail || ''"
                  :opening="isOpening(idx)"
                  @play="play({ skipToUri: track.uri })"
                >
                  <template #menu>
                    <SpotifyTrackMenu :track="track" kind="artist" :page-uri="uri"
                      @artist="$emit('select-artist', $event, rowKey(track, idx))"
                      @album="$emit('select-album', track.album, rowKey(track, idx))"
                      @radio="$emit('select-radio', $event, rowKey(track, idx))" />
                  </template>
                </TrackRow>
              </div>
            </ShowMoreClip>
          </section>

          <!-- The page without its tracks: the popular ones are missing, said
               with a retry rather than left out in silence. -->
          <MessageContent v-else-if="trackError" icon="spotify" :title="errorTitle(trackError)" :cta-label="t('spotify.retry')"
            cta-variant="control" :cta-click="load" />

          <!-- The tracks without the page: what failed is said, with a retry. -->
          <MessageContent v-if="pageError" icon="spotify" :title="errorTitle(pageError)" :cta-label="t('spotify.retry')"
            cta-variant="control" :cta-click="load" />

          <!-- Spotify's own sections, in its order and under its titles; its
               popular releases are the discography's first list. -->
          <template v-for="section in page?.sections ?? []" :key="section.id">
            <SpotifyDiscography v-if="section.groups" :groups="section.groups" :group="state.discography"
              @update:group="state.discography = $event" @select="$emit('select', $event)"
              @show-all="$emit('show-discography', { uri, group: $event })" />
            <section v-else class="section">
              <SpotifySectionTitle :title="section.title || ''" :linked="section.items.length >= columns"
                @open="$emit('show-section', section)" />
              <SpotifyShelfRow :items="section.items" @select="$emit('select', $event)" />
            </section>
          </template>
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { computed, inject, onBeforeUnmount, reactive, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSpotifyStore } from '@/stores/spotifyStore';
import { NAVIGATION_ENTRY_STATE } from '@/composables/useNavigationStack';
import { useSpotifyListingPlayback } from '@/composables/useSpotifyListingPlayback';
import { useCardGridColumns } from '@/composables/useCardGridColumns';
import MessageContent from '@/components/ui/MessageContent.vue';
import DetailHeader from '@/components/audio/DetailHeader.vue';
import TrackRow from '@/components/audio/TrackRow.vue';
import SpotifyTrackMenu from '@/components/spotify/SpotifyTrackMenu.vue';
import SpotifyShelfRow from '../SpotifyShelfRow.vue';
import SpotifyDiscography from '../SpotifyDiscography.vue';
import SpotifySectionTitle from '../SpotifySectionTitle.vue';
import ShowMoreClip from '../ShowMoreClip.vue';
import { musicPlaceholder } from '@/constants/placeholders';

const props = defineProps({
  uri: {
    type: String,
    required: true,
  },
  // What the page was opened with, until Spotify's page names the artist.
  name: {
    type: String,
    default: '',
  },
  image: {
    type: String,
    default: '',
  },
});

// `select`: a card of the page, with the kind of page it opens;
// `show-discography`: { uri, group }, the discography whole, on the list picked;
// `show-section`: a section whose row shows less than it holds, to open whole.
defineEmits(['select', 'select-artist', 'select-album', 'select-radio', 'show-discography', 'show-section']);

// The artist's listing opens on its popular tracks, then every track of every
// release: the ten first are the ones Spotify ranks (measured on two artists,
// the first five being the five its page names). Five show, as in its apps,
// the sixth peeking under the fade.
const POPULAR = 10;
const POPULAR_FIRST = 5;

const { t } = useI18n();
const store = useSpotifyStore();
// A row shows a quarter less than a column count: holding as many, it hides some.
const { columns } = useCardGridColumns();

// Kept in the navigation entry: going back finds the list as it was left.
const state = inject(NAVIGATION_ENTRY_STATE, null)?.() ?? reactive({});

const page = computed(() => store.artists[props.uri] ?? null);
const pageError = computed(() => store.artistErrors[props.uri] ?? null);
const listing = computed(() => store.contexts[props.uri] ?? null);
const trackError = computed(() => store.contextErrors[props.uri] ?? null);
const tracks = computed(() => listing.value?.tracks ?? []);
const { rowSong, rowKey, isOpening, isCurrent, play, shufflePlay } = useSpotifyListingPlayback(() => props.uri, tracks);

// Until the first ones are described: the listing answers within a second.
const tracksPending = computed(() => !tracks.value.length && !listing.value?.complete && !trackError.value);
const errorTitle = (error) => (error === 'not_signed_in' ? t('spotify.signingIn') : t('spotify.listUnavailable'));

// The ten, kept the same array while they stay the same tracks: the listing
// is replaced at every round of its loading, which would re-measure the clip
// in the middle of a reveal.
let lastTop = [];
const topTracks = computed(() => {
  const next = tracks.value.slice(0, POPULAR);
  if (next.length === lastTop.length && next.every((track, i) => track.uri === lastTop[i].uri)) return lastTop;
  lastTop = next;
  return next;
});
const popular = computed(() =>
  topTracks.value.slice(0, state.popularExpanded ? POPULAR : POPULAR_FIRST + 1)
);
const canShowMore = computed(() => !state.popularExpanded && tracks.value.length > POPULAR_FIRST);

// The artist's photo from the page: a track's cover is an album's, which is
// what a page opened from the player's artist line used to show.
const headerTitle = computed(() =>
  page.value?.name
  || props.name
  || tracks.value[0]?.artists.find((a) => a.uri === props.uri)?.name
  || ''
);
const headerImage = computed(() => page.value?.image || props.image || '');

let controller = null;
function load() {
  controller?.abort();
  controller = new AbortController();
  store.loadArtist(props.uri, { force: !!pageError.value });
  if (!listing.value?.complete) store.loadContext(props.uri, { signal: controller.signal });
}

onBeforeUnmount(() => controller?.abort());

// The store forgot the page (another language): asked again in this one.
watch(() => store.artists[props.uri], (now, before) => {
  if (before && !now) load();
});

load();
</script>

<style scoped>
.spotify-artist {
  display: flex;
  flex-direction: column;
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

.section-title {
  color: var(--color-text);
  margin: 0;
}

.tracks {
  display: flex;
  flex-direction: column;
}
</style>
