<template>
  <div class="artist-view">
    <div class="transition-container">
      <Transition name="content-swap">
        <MessageContent v-if="loading && !artist" key="loading" loading :title="t('musicLibrary.loading')" />
        <MessageContent v-else-if="!artist" key="notfound" :title="t('musicLibrary.notFound')" />
        <div v-else key="loaded" class="content-stack">
          <DetailHeader
            :image-src="store.coverUrl(artist.coverArt, 600)"
            :fallback="musicPlaceholder"
            :title="artist.name"
            :subtitle-meta="subtitleMeta"
            :show-play="albums.length > 0"
            :show-shuffle="false"
            :play-loading="playLoading"
            @play="playAll"
          />

          <div class="albums-grid">
            <AlbumCard v-for="album in albums" :key="album.id" :album="album" @click="$emit('select-album', album)" />
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
import MessageContent from '@/components/ui/MessageContent.vue';
import DetailHeader from '@/components/audio/DetailHeader.vue';
import AlbumCard from '../cards/AlbumCard.vue';
import { musicPlaceholder } from '@/constants/placeholders';

const props = defineProps({
  artistId: {
    type: String,
    required: true,
  },
});

defineEmits(['select-album']);

const { t } = useI18n();
const store = useMusicLibraryStore();

const artist = ref(null);
const loading = ref(false);
const playLoading = ref(false);

const albums = computed(() => artist.value?.album || []);

// The artist's photo, counts and years are all the catalog knows about them:
// getArtist carries no biography and Milō queries nothing else for one, so
// everything the header shows is derived from the albums already on screen.

// Navidrome tags genres per album, never per artist — so the artist's is the
// one their albums carry most often (ties keep album order).
const genre = computed(() => {
  const counts = new Map();
  for (const album of albums.value) {
    for (const tag of album.genres || []) {
      if (tag?.name) counts.set(tag.name, (counts.get(tag.name) || 0) + 1);
    }
  }
  let best = '';
  for (const [name, count] of counts) {
    if (!best || count > counts.get(best)) best = name;
  }
  return best;
});

const subtitleMeta = computed(() => {
  const list = albums.value;
  if (!list.length) return '';
  const parts = [];
  if (genre.value) parts.push(genre.value);
  // Counted from the albums this page shows, not artist.albumCount: that one is
  // Navidrome's answer across every library, including storage spaces that are
  // unmounted or out of the active scope (see the backend's get_artist).
  parts.push(t('musicLibrary.albumsCount', { count: list.length }));
  const years = list.map((album) => album.year).filter(Boolean);
  if (years.length) {
    const first = Math.min(...years);
    const last = Math.max(...years);
    parts.push(first === last ? String(first) : `${first} – ${last}`);
  }
  return parts.join(' · ');
});

// Every album, in the order the page shows them. The artist payload holds no
// tracks at all (getArtist answers albums only), so the queue is assembled here
// with one album fetch per release — merged multi-disc ids included, which
// fetchAlbum expands into their concatenated tracks.
async function playAll() {
  if (playLoading.value || !albums.value.length) return;
  const requested = props.artistId;
  playLoading.value = true;
  const fetched = await Promise.all(albums.value.map((album) => store.fetchAlbum(album.id)));
  playLoading.value = false;
  // Navigated to another artist while the fetches were in flight: playing now
  // would start a queue the user has left behind.
  if (props.artistId !== requested) return;
  const songs = fetched.flatMap((album) => album?.song || []);
  if (songs.length) store.playContext(songs, 0, false);
}

watch(() => props.artistId, async (id) => {
  loading.value = true;
  artist.value = await store.fetchArtist(id);
  loading.value = false;
}, { immediate: true });
</script>

<style scoped>
.artist-view {
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
