<template>
  <div class="artist-view">
    <div class="swap-stack">
      <Transition name="fade-slide">
        <MessageContent v-if="error === 'unavailable'" key="unavailable" icon="musicNote"
          :title="t('musicLibrary.catalogUnreachable')" :cta-label="t('musicLibrary.retry')"
          cta-variant="control" :cta-loading="loading" :cta-click="reload" />
        <MessageContent v-else-if="error" key="notfound" icon="musicNote" :title="t('musicLibrary.notFound')" />
        <div v-else-if="!artist" key="loading" class="content-stack swap-skeleton" aria-hidden="true">
          <SkeletonDetailHeader />
          <div class="albums-grid">
            <SkeletonAlbumCard v-for="i in 12" :key="i" />
          </div>
        </div>
        <div v-else key="loaded" class="content-stack">
          <DetailHeader
            :image-src="store.coverUrl(artist.coverArt, 600)"
            :fallback="musicPlaceholder"
            :title="artist.name"
            :subtitle-meta="subtitleMeta"
            :show-play="albums.length > 0"
            :playing="header.playing.value"
            :play-loading="header.loading.value"
            @play="header.play"
            @pause="header.pause"
          >
            <template v-if="albums.length" #actions>
              <LibraryItemMenu trigger="header" :songs="artistSongs" shuffle @shuffle="header.shuffle" />
            </template>
          </DetailHeader>

          <div class="albums-grid">
            <AlbumCard v-for="album in albums" :key="album.id" :album="album" @click="$emit('select-album', album)" />
          </div>
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import MessageContent from '@/components/ui/MessageContent.vue';
import DetailHeader from '@/components/audio/DetailHeader.vue';
import SkeletonDetailHeader from '@/components/audio/SkeletonDetailHeader.vue';
import AlbumCard from '../cards/AlbumCard.vue';
import LibraryItemMenu from '../LibraryItemMenu.vue';
import SkeletonAlbumCard from '../cards/SkeletonAlbumCard.vue';
import { musicPlaceholder } from '@/constants/placeholders';
import { useDetailPlayback } from '@/composables/useDetailPlayback';
import { useCatalogRead } from '@/composables/useCatalogRead';

const props = defineProps({
  artistId: {
    type: String,
    required: true,
  },
});

defineEmits(['select-album']);

const { t, formatList } = useI18n();
const store = useMusicLibraryStore();
const unifiedStore = useUnifiedAudioStore();

const { value: artist, error, loading, reload } = useCatalogRead(() => props.artistId, store.fetchArtist);

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

// Every album's songs, in the order the page shows them — all of them or
// none (fetchAlbumsSongs). Null when the page moved to another artist while the
// fetches were in flight: playing or queueing then would act on an artist the
// user has left behind. Null too when an album did not load, and the banner
// names which: the play button stopped turning and nothing played or queued,
// without a word about why.
async function artistSongs() {
  if (!albums.value.length) return null;
  const requested = props.artistId;
  const asked = albums.value;
  const { value, failed } = await store.fetchAlbumsSongs(asked.map((album) => album.id));
  if (props.artistId !== requested) return null;
  if (failed.length) {
    const names = asked.filter((album) => failed.includes(album.id)).map((album) => album.name);
    unifiedStore.transientNotice = {
      title: t('musicLibrary.albumsNotLoaded', { count: failed.length }),
      detail: formatList(names),
    };
  }
  return value;
}

// The header's spinner covers the fetches too: it turns from the press.
async function playAll(shuffle) {
  const contextId = `artist:${props.artistId}`;
  const songs = await artistSongs();
  if (!songs?.length) return false;
  return shuffle ? store.playShuffled(songs, contextId) : store.playContext(songs, 0, false, contextId);
}

const header = useDetailPlayback(
  'music_library', () => store.currentContextId === `artist:${props.artistId}`, playAll,
);
</script>

<style scoped>
.artist-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
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
