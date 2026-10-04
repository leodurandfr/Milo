<template>
  <div class="spotify-context">
    <div class="transition-container">
      <Transition name="content-swap">
        <MessageContent v-if="error" key="error" icon="network"
          :title="error === 'not_signed_in' ? t('spotify.signingIn') : t('spotify.listUnavailable')"
          :cta-label="t('spotify.retry')" cta-variant="background-strong" :cta-click="load" />

        <MessageContent v-else-if="!listing?.ready" key="loading" loading
          :title="progress" />

        <MessageContent v-else-if="!tracks.length" key="empty" :title="t('spotify.noTracks')" />

        <div v-else key="loaded" class="content-stack">
          <DetailHeader
            :image-src="headerImage"
            :fallback="musicPlaceholder"
            :icon="headerIcon"
            :title="headerTitle"
            :subtitle="headerSubtitle"
            :subtitle-meta="t('spotify.tracksCount', { count: tracks.length })"
            @play="play()"
            @shuffle="shufflePlay"
          />

          <div class="tracks">
            <TrackRow
              v-for="(track, idx) in tracks"
              :key="`${track.uri}-${idx}`"
              :song="rowSong(track)"
              :number="kind === 'album' ? (track.track_number || idx + 1) : idx + 1"
              :current="isCurrent(track)"
              :playing="store.isPlaying"
              show-artist
              :show-cover="kind !== 'album'"
              :cover-url="track.thumbnail || ''"
              :artist-link="kind !== 'artist' && !!track.artists[0]?.uri"
              :show-menu="kind !== 'album' && !!track.album.uri"
              :menu-label="t('spotify.goToAlbum')"
              @play="play({ skipToUri: track.uri })"
              @artist="$emit('select-artist', track.artists[0])"
              @menu="$emit('select-album', track.album)"
            />
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
import { musicPlaceholder } from '@/constants/placeholders';

const props = defineProps({
  uri: {
    type: String,
    required: true,
  },
  // 'playlist' | 'liked' | 'album' | 'artist'
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

defineEmits(['select-artist', 'select-album']);

const { t } = useI18n();
const store = useSpotifyStore();

const listing = computed(() => store.contexts[props.uri] ?? null);
const error = computed(() => store.contextErrors[props.uri] ?? null);
const tracks = computed(() => listing.value?.tracks ?? []);

const progress = computed(() => {
  const l = listing.value;
  if (!l?.length) return t('spotify.loadingTracks');
  return t('spotify.loadingTracksProgress', { loaded: l.cached, total: l.length });
});

// An album and an artist are named by their tracks once listed: they are
// reached from a track, which only knows the name it carries.
const first = computed(() => tracks.value[0] ?? null);
const headerTitle = computed(() => {
  if (props.kind === 'liked') return t('spotify.likedSongs');
  if (props.name) return props.name;
  if (props.kind === 'album') return first.value?.album.name || '';
  if (props.kind === 'artist') {
    return first.value?.artists.find((a) => a.uri === props.uri)?.name || '';
  }
  return t('spotify.untitledPlaylist');
});
const headerSubtitle = computed(() => {
  if (props.kind === 'album') return first.value?.artists.map((a) => a.name).join(', ') || '';
  if (props.kind === 'artist') return t('spotify.artist');
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

function rowSong(track) {
  return {
    title: track.title,
    artist: track.artists.map((a) => a.name).join(', '),
    duration: (track.duration_ms || 0) / 1000,
  };
}

// The row playing now: this track, played from this list — the same track in
// another playlist is not this row.
function isCurrent(track) {
  return track.uri === store.currentTrackUri && store.currentContextUri === props.uri;
}

function play({ skipToUri = null, shuffle = false } = {}) {
  store.playContext(props.uri, { skipToUri, shuffle });
}

// The first track of a shuffled play is picked here, from the whole listing on
// screen: go-librespot starts a context from a signed-in idle state with its
// shuffle off, so it cannot be left to pick (measured).
function shufflePlay() {
  // A local file in a playlist lists here but cannot be played from Milō.
  const list = tracks.value.filter((track) => !track.uri.startsWith('spotify:local:'));
  if (!list.length) return;
  const start = list[Math.floor(Math.random() * list.length)];
  play({ skipToUri: start.uri, shuffle: true });
}

let controller = null;
function load() {
  controller?.abort();
  controller = new AbortController();
  store.loadContext(props.uri, { signal: controller.signal, force: true });
}

onBeforeUnmount(() => controller?.abort());

if (!listing.value?.ready) load();
</script>

<style scoped>
.spotify-context {
  display: flex;
  flex-direction: column;
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

.tracks {
  display: flex;
  flex-direction: column;
}
</style>
