<template>
  <div class="album-view">
    <MessageContent v-if="loading && !album" loading :title="t('musicLibrary.loading')" />
    <MessageContent v-else-if="!album" :title="t('musicLibrary.notFound')" />

    <template v-else>
      <TracklistHeader
        :cover-id="album.coverArt"
        :title="album.name"
        :subtitle="subtitle"
        show-favorite
        :is-favorite="albumStarred"
        @play="playFrom(0)"
        @shuffle="shufflePlay"
        @toggle-favorite="store.toggleStar('album', album.id, album.starred)"
      >
        <template #actions>
          <IconButton
            icon="threeDots"
            variant="background-strong"
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
            show-menu
            @play="playFrom(item.index)"
            @menu="store.requestAddToPlaylist([item.song.id])"
          />
        </template>
      </div>
    </template>
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
import TrackRow from '../cards/TrackRow.vue';

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
  if (album.value.artist) parts.push(album.value.artist);
  if (album.value.year) parts.push(String(album.value.year));
  parts.push(t('musicLibrary.tracksCount', { count: album.value.songCount || songs.value.length }));
  const mins = totalMinutes(album.value.duration);
  if (mins) parts.push(`${mins} ${t('musicLibrary.minutesShort')}`);
  return parts.join(' · ');
});

function playFrom(index) {
  store.playContext(songs.value, index, false);
}

function shufflePlay() {
  if (!songs.value.length) return;
  // Pin a random track first, backend shuffles the remainder → true shuffle feel.
  const start = Math.floor(Math.random() * songs.value.length);
  store.playContext(songs.value, start, true);
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

.tracks {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.disc-header {
  margin: 0;
  padding: var(--space-03) var(--space-03) var(--space-01);
  color: var(--color-text-secondary);
  text-transform: uppercase;
}
</style>
