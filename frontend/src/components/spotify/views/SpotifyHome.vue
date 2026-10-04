<template>
  <div class="spotify-home">
    <div class="transition-container">
      <Transition name="content-swap">
        <!-- Nobody signed in, nobody kept: only a cast can bring an account. -->
        <MessageContent v-if="castFirst" key="cast"
          :title="t('spotify.castFirstTitle')" :subtitle="t('spotify.castFirstSubtitle')" />

        <MessageContent v-else-if="!home && (store.signingIn || store.homeError === 'not_signed_in')"
          key="signing-in" loading :title="t('spotify.signingIn')" />

        <MessageContent v-else-if="store.homeError === 'unavailable'" key="error" icon="network"
          :title="t('spotify.libraryUnavailable')"
          :cta-label="t('spotify.retry')" cta-variant="control"
          :cta-click="() => store.loadHome({ force: true })" />

        <div v-else-if="!home" key="loading" class="sections">
          <section class="section">
            <div class="shortcuts-grid">
              <div v-for="i in 8" :key="`shortcut-${i}`" class="shortcut-skeleton shimmer" />
            </div>
          </section>
          <section class="section">
            <div class="skeleton-text-line section-skeleton-title shimmer" />
            <div class="cards-grid">
              <SkeletonSpotifyCard v-for="i in columns" :key="`card-${i}`" />
            </div>
          </section>
        </div>

        <div v-else key="loaded" class="sections">
          <section class="section">
            <div class="shortcuts-grid">
              <SpotifyShortcutTile v-for="item in shortcuts" :key="item.uri"
                :liked="item.kind === 'liked'"
                :title="item.kind === 'liked' ? t('spotify.likedSongs') : item.name || t('spotify.untitledPlaylist')"
                :image="item.image || ''" @click="$emit('select', item)" />
            </div>
          </section>

          <!-- Spotify's own shelves, in its order and under its titles. -->
          <section v-for="shelf in home.shelves" :key="shelf.id" class="section">
            <h2 class="section-title heading-2">{{ shelf.title }}</h2>
            <SpotifyShelfRow :items="shelf.items" @select="$emit('select', $event)" />
          </section>

          <section v-for="section in librarySections" :key="section.key" class="section">
            <header class="section-header">
              <span class="section-overline text-mono-small">{{ t('spotify.libraryOverline') }}</span>
              <h2 class="section-title heading-2">{{ section.title }}</h2>
            </header>
            <SpotifyPlaylistGrid :items="section.items" :state-key="`rows:${section.key}`"
              @select="$emit('select', { ...$event, kind: 'playlist' })" />
          </section>
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { computed, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSpotifyStore } from '@/stores/spotifyStore';
import { useCardGridColumns } from '@/composables/useCardGridColumns';
import MessageContent from '@/components/ui/MessageContent.vue';
import SpotifyShelfRow from '../SpotifyShelfRow.vue';
import SpotifyPlaylistGrid from '../SpotifyPlaylistGrid.vue';
import SpotifyShortcutTile from '../cards/SpotifyShortcutTile.vue';
import SkeletonSpotifyCard from '../cards/SkeletonSpotifyCard.vue';

// A card or a tile, with the kind of page it opens: playlist, album, artist or liked.
defineEmits(['select']);

const { t } = useI18n();
const store = useSpotifyStore();
const { columns } = useCardGridColumns();

const home = computed(() => store.home);
const castFirst = computed(() => !store.account && !store.signingIn);

// The tiles above the shelves: Spotify's shortcuts, as many as its app shows.
// Without its home, Liked Songs alone.
const SHORTCUTS = 8;
const shortcuts = computed(() => {
  const h = home.value;
  if (h.shortcuts.length) return h.shortcuts.slice(0, SHORTCUTS);
  return [{ uri: h.liked_songs_uri, kind: 'liked' }];
});

// The account's own playlists, after Spotify's shelves: the home lists only a
// few of them. An empty section is left out.
const librarySections = computed(() => {
  const p = home.value.playlists;
  return [
    { key: 'mine', title: t('spotify.yourPlaylists'), items: p.mine },
    { key: 'followed', title: t('spotify.followedPlaylists'), items: p.followed },
  ].filter((section) => section.items.length > 0);
});

// The library answers once the daemon is signed in — the audio state names the
// account a moment before that (a start, a profile switch): it loads then, and
// again if a first try met the sign-in still in progress.
watch(() => store.account && !store.signingIn, (signedIn) => {
  if (signedIn) store.loadHome({ force: store.homeError !== null });
}, { immediate: true });
</script>

<style scoped>
.spotify-home {
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

.section-header {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.section-overline {
  color: var(--color-brand);
}

.section-title {
  color: var(--color-text);
  margin: 0;
}

.shortcuts-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-02);
}

.shortcut-skeleton {
  height: 56px;
  border-radius: var(--radius-02);
}

.section-skeleton-title {
  width: 40%;
}

.cards-grid {
  display: grid;
  grid-template-columns: repeat(var(--card-grid-columns), minmax(0, 1fr));
  row-gap: var(--space-05);
  column-gap: var(--space-03);
}
</style>
