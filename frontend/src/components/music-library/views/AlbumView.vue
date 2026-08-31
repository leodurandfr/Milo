<template>
  <div class="album-view">
    <div class="transition-container">
      <Transition name="content-swap">
        <MessageContent v-if="loading && !album" key="loading" loading :title="t('musicLibrary.loading')" />
        <MessageContent v-else-if="!album" key="notfound" :title="t('musicLibrary.notFound')" />

        <div v-else key="loaded" class="content-stack">
          <DetailHeader
            :image-src="store.coverUrl(album.coverArt, 600)"
            :fallback="musicPlaceholder"
            :title="album.name"
            :subtitle="subtitleArtist"
            :subtitle-meta="subtitleMeta"
            :subtitle-clickable="!isVariousArtists"
            :show-shuffle="false"
            @play="playFrom(0)"
            @select-artist="selectArtist"
          />

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
import { totalMinutes, formatAudioQuality } from '../format.js';
import MessageContent from '@/components/ui/MessageContent.vue';
import DetailHeader from '@/components/audio/DetailHeader.vue';
import TrackRow from '@/components/audio/TrackRow.vue';
import { musicPlaceholder } from '@/constants/placeholders';

const props = defineProps({
  albumId: {
    type: String,
    required: true,
  },
});

const emit = defineEmits(['select-artist']);

const { t } = useI18n();
const store = useMusicLibraryStore();

const album = ref(null);
const loading = ref(false);

const songs = computed(() => album.value?.song || []);

// Canonical album-artist id (OpenSubsonic). Featured guests on a track are the
// entries in song.artists[] beyond this one.
const albumArtistId = computed(() => album.value?.artistId ?? null);

// A true "Various Artists" release: the album artist isn't credited as the PRIMARY
// artist on a majority of tracks. A single-artist album keeps its artist as primary
// on (nearly) every track, so an occasional mistagged outlier (a posse-cut or skit
// whose own artist tag names only a guest, with nothing pointing back to the real
// album artist) doesn't flip it — only a majority mismatch does. True compilations
// have no single artist covering a majority, however the tracks are tagged (even
// when they share some other uniform album-level credit, since that never appears
// as any individual track's own primary artist).
const isVariousArtists = computed(() => {
  const list = songs.value;
  if (!list.length) return false;
  const id = albumArtistId.value;
  const name = album.value?.artist;
  const matches = list.filter((s) => {
    if (s.artists?.length) return id ? s.artists[0].id === id : s.artists[0].name === name;
    return name ? s.artist === name : false;
  });
  return matches.length * 2 <= list.length;
});

// song.id → "Guest A, Guest B": a track's artists minus the album artist. Covers
// both a genuine guest trailing the primary artist AND a mistagged outlier track
// whose own artist tag names someone else entirely (no album artist in artists[]
// at all) — that track still gets a "feat." label naming whoever IS credited,
// rather than showing nothing. Empty for true compilations (which show the full
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

const subtitleArtist = computed(() => {
  if (!album.value) return '';
  if (isVariousArtists.value) return t('musicLibrary.variousArtists');
  return album.value.artist || '';
});

const subtitleMeta = computed(() => {
  if (!album.value) return '';
  const parts = [];
  if (album.value.year) parts.push(String(album.value.year));
  parts.push(t('musicLibrary.tracksCount', { count: album.value.songCount || songs.value.length }));
  const mins = totalMinutes(album.value.duration);
  if (mins) parts.push(`${mins} ${t('musicLibrary.minutesShort')}`);
  const quality = formatAudioQuality(songs.value[0]?.bitDepth, songs.value[0]?.samplingRate);
  if (quality) parts.push(quality);
  return parts.join(' · ');
});

function playFrom(index) {
  store.playContext(songs.value, index, false);
}

function selectArtist() {
  if (albumArtistId.value) emit('select-artist', { id: albumArtistId.value, name: album.value.artist });
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
