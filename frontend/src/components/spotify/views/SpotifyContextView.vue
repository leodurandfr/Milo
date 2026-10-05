<template>
  <div class="spotify-context">
    <div class="swap-stack">
      <Transition name="fade-slide">
        <!-- Rows already listed stay over an error: reopening asks for the rest. -->
        <MessageContent v-if="error && !tracks.length" key="error" icon="network"
          :title="error === 'not_signed_in' ? t('spotify.signingIn') : t('spotify.listUnavailable')"
          :cta-label="t('spotify.retry')" cta-variant="control" :cta-click="load" />

        <MessageContent v-else-if="!tracks.length && !listing?.complete" key="loading" loading
          :title="progress" />

        <MessageContent v-else-if="!tracks.length" key="empty" :title="t('spotify.noTracks')" />

        <div v-else key="loaded" class="content-stack">
          <DetailHeader
            :image-src="headerImage"
            :fallback="musicPlaceholder"
            :icon="headerIcon"
            :title="headerTitle"
            :subtitle="headerSubtitle"
            :subtitle-meta="t('spotify.tracksCount', { count: trackCount })"
            @play="play()"
            @shuffle="shufflePlay"
          />

          <div class="tracks">
            <TrackRow
              v-for="(track, idx) in visibleTracks"
              :key="`${track.uri}-${idx}`"
              :song="rowSong(track)"
              :number="kind === 'album' ? (track.track_number || idx + 1) : idx + 1"
              :current="isCurrent(track)"
              :playing="store.isPlaying"
              show-artist
              :show-cover="kind !== 'album'"
              :cover-url="track.thumbnail || ''"
              :artist-link="!!track.artists[0]?.uri"
              @play="play({ skipToUri: track.uri })"
              @artist="$emit('select-artist', track.artists[0])"
            >
              <template #menu>
                <SpotifyTrackMenu :track="track" :kind="kind"
                  @artist="$emit('select-artist', track.artists[0])"
                  @album="$emit('select-album', track.album)"
                  @radio="$emit('select-radio', $event)" />
              </template>
            </TrackRow>
            <div v-if="hasMore" ref="sentinelRef" aria-hidden="true"></div>
          </div>
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSpotifyStore } from '@/stores/spotifyStore';
import MessageContent from '@/components/ui/MessageContent.vue';
import DetailHeader from '@/components/audio/DetailHeader.vue';
import TrackRow from '@/components/audio/TrackRow.vue';
import SpotifyTrackMenu from '@/components/spotify/SpotifyTrackMenu.vue';
import { musicPlaceholder } from '@/constants/placeholders';
import { useRenderWindow } from '@/composables/useRenderWindow';
import { useSpotifyListingPlayback } from '@/composables/useSpotifyListingPlayback';

const props = defineProps({
  uri: {
    type: String,
    required: true,
  },
  // 'playlist' | 'liked' | 'album' (an artist has its own page)
  kind: {
    type: String,
    required: true,
  },
  // What the page was opened with; the listing names what it can on its own.
  name: {
    type: String,
    default: '',
  },
  image: {
    type: String,
    default: '',
  },
  owner: {
    type: String,
    default: '',
  },
});

defineEmits(['select-artist', 'select-album', 'select-radio']);

const { t } = useI18n();
const store = useSpotifyStore();

const listing = computed(() => store.contexts[props.uri] ?? null);
const error = computed(() => store.contextErrors[props.uri] ?? null);
const tracks = computed(() => listing.value?.tracks ?? []);
// The rows mounted, out of the tracks described so far.
const { visible: visibleTracks, hasMore, sentinelRef } = useRenderWindow(tracks);
const { rowSong, isCurrent, play, shufflePlay } = useSpotifyListingPlayback(() => props.uri, tracks);
// While the rest is described, the listing's own length.
const trackCount = computed(() => (listing.value?.complete ? tracks.value.length : listing.value?.length ?? 0));

const progress = computed(() => {
  const l = listing.value;
  if (!l?.length) return t('spotify.loadingTracks');
  return t('spotify.loadingTracksProgress', { loaded: l.cached, total: l.length });
});

// An album is named by its tracks once listed: it is reached from a track,
// which only knows the name it carries.
const first = computed(() => tracks.value[0] ?? null);
const headerTitle = computed(() => {
  if (props.kind === 'liked') return t('spotify.likedSongs');
  if (props.name) return props.name;
  if (props.kind === 'album') return first.value?.album.name || '';
  return t('spotify.untitledPlaylist');
});
const headerSubtitle = computed(() => {
  if (props.kind === 'album') return first.value?.artists.map((a) => a.name).join(', ') || '';
  if (props.kind === 'playlist' && props.owner === 'spotify') return 'Spotify';
  return '';
});
const headerIcon = computed(() => {
  if (props.kind === 'liked') return 'heart';
  return '';
});
const headerImage = computed(() => {
  if (props.kind === 'liked') return '';
  return props.image || first.value?.artwork || '';
});

let controller = null;
function load() {
  controller?.abort();
  controller = new AbortController();
  store.loadContext(props.uri, { signal: controller.signal });
}

onBeforeUnmount(() => controller?.abort());

if (!listing.value?.complete) load();
</script>

<style scoped>
.spotify-context {
  display: flex;
  flex-direction: column;
}

.content-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.tracks {
  display: flex;
  flex-direction: column;
}
</style>
