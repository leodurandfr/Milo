<template>
  <div class="album-view">
    <div class="transition-container">
      <Transition name="content-swap">
        <MessageContent v-if="loading && !album" key="loading" loading :title="t('musicLibrary.loading')" />
        <MessageContent v-else-if="!album" key="notfound" :title="t('musicLibrary.notFound')" />

        <div v-else key="loaded" class="content-stack">
          <TracklistHeader
            :cover-id="album.coverArt"
            :title="album.name"
            :subtitle="subtitle"
            show-favorite
            :show-shuffle="false"
            :is-favorite="albumStarred"
            @play="playFrom(0)"
            @toggle-favorite="store.toggleStar('album', album.id, album.starred)"
          >
            <template #actions>
              <IconButton
                icon="threeDots"
                variant="on-dark"
                :aria-label="t('musicLibrary.playlists.addToPlaylist')"
                @click="addAlbumToPlaylist"
              />
            </template>
          </TracklistHeader>

          <div class="tracks">
            <template v-for="group in discGroups" :key="group.disc">
              <!-- Disc separator, only for genuine multi-disc releases. -->
              <p v-if="multiDisc" class="disc-header text-mono-small">
                {{ group.title || t('musicLibrary.discLabel', { number: group.disc }) }}
              </p>
              <TrackRow
                v-for="(item, i) in group.songs"
                :key="item.song.id"
                :song="item.song"
                :number="item.song.track || i + 1"
                :current="item.song.id === store.currentTrackId"
                :playing="store.isPlaying"
                :show-artist="isVariousArtists"
                :feat="featuredBySong[item.song.id] || ''"
                show-menu
                @play="playFrom(item.index)"
                @menu="store.requestAddToPlaylist([item.song.id])"
              />
            </template>
          </div>
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import { totalMinutes } from '../format.js';
import MessageContent from '@/components/ui/MessageContent.vue';
import IconButton from '@/components/ui/IconButton.vue';
import TracklistHeader from '../cards/TracklistHeader.vue';
import TrackRow from '@/components/audio/TrackRow.vue';

const props = defineProps({
  albumId: {
    type: String,
    required: true,
  },
});

const { t } = useI18n();
const store = useMusicLibraryStore();

const album = ref(null);
const loading = ref(false);

const songs = computed(() => album.value?.song || []);

// Canonical album-artist id (OpenSubsonic). Featured guests on a track are the
// entries in song.artists[] beyond this one.
const albumArtistId = computed(() => album.value?.artistId ?? null);

// A true "Various Artists" release: at least one track's PRIMARY artist isn't the
// album artist. A single-artist album with featured guests keeps the album artist
// primary on every track (guests only trail it), so it stays non-various and
// instead surfaces per-track "feat." labels. Falls back to distinct display names
// when a source doesn't provide the structured artists[] array.
const isVariousArtists = computed(() => {
  const list = songs.value;
  if (!list.length) return false;
  const withArtists = list.filter((s) => s.artists?.length);
  if (!withArtists.length) {
    return new Set(list.map((s) => s.artist).filter(Boolean)).size > 1;
  }
  const id = albumArtistId.value;
  const name = album.value?.artist;
  return withArtists.some((s) =>
    id ? s.artists[0].id !== id : s.artists[0].name !== name
  );
});

// song.id → "Guest A, Guest B": a track's artists minus the album artist. Empty
// for a pure album-artist track and for true compilations (which show the full
// per-track artist instead, via show-artist).
const featuredBySong = computed(() => {
  const map = {};
  if (isVariousArtists.value) return map;
  const id = albumArtistId.value;
  const name = album.value?.artist;
  for (const s of songs.value) {
    const extra = (s.artists || [])
      .filter((a) => (id ? a.id !== id : a.name !== name))
      .map((a) => a.name)
      .filter(Boolean);
    if (extra.length) map[s.id] = extra.join(', ');
  }
  return map;
});

// Disc-number → subtitle, when the release carries per-disc titles (OpenSubsonic
// discTitles, e.g. "Bonus Remixes"). Absent for most albums → we fall back to
// the generic "Disc N" label.
const discTitles = computed(() => {
  const map = {};
  for (const dt of album.value?.discTitles || []) {
    if (dt && dt.disc != null) map[dt.disc] = dt.title;
  }
  return map;
});

// Group the (disc-then-track ordered) songs by disc number, carrying each song's
// flat index so play actions still target the right position in the full album
// order. Grouped by value rather than consecutive runs so a stray out-of-order
// track can't spawn a duplicate disc heading.
const discGroups = computed(() => {
  const byDisc = new Map();
  songs.value.forEach((song, index) => {
    const disc = Number(song.discNumber) || 1;
    if (!byDisc.has(disc)) byDisc.set(disc, []);
    byDisc.get(disc).push({ song, index });
  });
  return [...byDisc.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([disc, groupSongs]) => ({ disc, title: discTitles.value[disc] || '', songs: groupSongs }));
});

const multiDisc = computed(() => discGroups.value.length > 1);

const albumStarred = computed(() =>
  album.value ? store.isStarred('album', album.value.id, album.value.starred) : false
);

const subtitle = computed(() => {
  if (!album.value) return '';
  const parts = [];
  if (isVariousArtists.value) parts.push(t('musicLibrary.variousArtists'));
  else if (album.value.artist) parts.push(album.value.artist);
  if (album.value.year) parts.push(String(album.value.year));
  parts.push(t('musicLibrary.tracksCount', { count: album.value.songCount || songs.value.length }));
  let result = parts.join(' · ');
  const mins = totalMinutes(album.value.duration);
  if (mins) result += `, ${mins} ${t('musicLibrary.minutesShort')}`;
  return result;
});

function playFrom(index) {
  store.playContext(songs.value, index, false);
}

// Add the whole album to a playlist (all tracks; the user prunes them afterwards
// in the playlist's edit mode).
function addAlbumToPlaylist() {
  const ids = songs.value.map((s) => s.id).filter(Boolean);
  if (ids.length) store.requestAddToPlaylist(ids);
}

watch(() => props.albumId, async (id) => {
  loading.value = true;
  album.value = await store.fetchAlbum(id);
  loading.value = false;
}, { immediate: true });
</script>

<style scoped>
.album-view {
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

.tracks {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.disc-header {
  margin: 0;
  padding: var(--space-03) var(--space-03) var(--space-01);
  color: var(--color-text-secondary);
  text-transform: uppercase;
}
</style>
