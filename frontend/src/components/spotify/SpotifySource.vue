<template>
  <!-- The navigation, and the full player it expands into: BrowserSourceViews
       swaps the two. -->
  <BrowserSourceViews source="spotify" :playback="playback"
    :can-open-album="!!nowPlaying?.albumUri" :artists="playerArtists"
    @title-click="openPlayerAlbum" @secondary-click="openArtist">
    <template #navigation="{ bar }">
      <AudioSourceLayout ref="audioLayoutRef" :show-player="shouldShowPlayer"
        :header-title="currentTitle" :header-show-back="canGoBack" :header-title-muted="PAGES.includes(currentView) && currentView !== 'section'"
        header-icon="spotify" gradient="spotify"
        :header-actions-key="currentView" :content-key="currentKey"
        :player-mobile-height="144" :pending-scroll-restore="pendingScrollRestore"
        @header-back="back" @scroll-restored="onScrollRestored" @pages-settled="pagesSettled">

        <!-- Home only: whose library this is, and the way to the others. -->
        <template v-if="currentView === 'home' && activeProfile" #header-actions>
          <button v-press type="button" class="profile-button" :aria-label="t('spotify.profiles')"
            @click="goTo('profiles')">
            <ProfileAvatar :key="activeProfile.username" :profile="activeProfile" />
          </button>
        </template>

        <!-- One page per stack entry, kept while the entry is on the stack. A
             branch reads its own entry, never the current view: a kept page
             re-renders from it. -->
        <template #pages>
          <KeepAlive :include="keptKeys" :max="KEPT_PAGES">
            <component :is="currentPage" :key="currentKey" v-slot="{ entry }">
              <SpotifyHome v-if="entry.view === 'home'" @select="openItem" @show-section="openSection" />

              <SpotifyProfilesView v-else-if="entry.view === 'profiles'" @picked="reset" />

              <SpotifyContextView v-else-if="entry.view === 'context'"
                :uri="entry.params.uri" :kind="entry.params.kind" :name="entry.params.name"
                :image="entry.params.image" :owner="entry.params.owner"
                @select-artist="openArtist" @select-album="openAlbum" @select-radio="openRadio" />

              <SpotifyArtistView v-else-if="entry.view === 'artist'"
                :uri="entry.params.uri" :name="entry.params.name" :image="entry.params.image"
                @select="openItem" @select-artist="openArtist" @select-album="openAlbum" @select-radio="openRadio"
                @show-discography="push('discography', $event)" @show-section="openSection" />

              <SpotifyDiscographyView v-else-if="entry.view === 'discography'"
                :uri="entry.params.uri" :group="entry.params.group" @select="openItem" />

              <SpotifySectionView v-else-if="entry.view === 'section'"
                :items="entry.params.items" @select="openItem" />
            </component>
          </KeepAlive>
        </template>

        <!-- Docked player: it reads what it draws from the state (no queue
             carousel: go-librespot does not say what comes next); the album
             and the artist it emits open here, in the navigation. -->
        <template #player>
          <AudioPlayer v-bind="bar" source="spotify"
            @title-click="openPlayerAlbum" @secondary-click="openArtist" />
        </template>
      </AudioSourceLayout>
    </template>
  </BrowserSourceViews>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { useSpotifyStore } from '@/stores/spotifyStore';
import { useNavigationStack } from '@/composables/useNavigationStack';
import { KEPT_PAGES } from '@/constants/navigation';
import { useSourcePlaybackVisibility } from '@/composables/useSourcePlaybackVisibility';
import { useSpotifyOpening } from '@/composables/useSpotifyOpening';
import { useI18n } from '@/services/i18n';
import AudioPlayer from '@/components/audio/AudioPlayer.vue';
import BrowserSourceViews from '@/components/audio/BrowserSourceViews.vue';
import AudioSourceLayout from '@/components/audio/AudioSourceLayout.vue';
import ProfileAvatar from './ProfileAvatar.vue';
import SpotifyHome from './views/SpotifyHome.vue';
import SpotifyContextView from './views/SpotifyContextView.vue';
import SpotifyArtistView from './views/SpotifyArtistView.vue';
import SpotifyDiscographyView from './views/SpotifyDiscographyView.vue';
import SpotifySectionView from './views/SpotifySectionView.vue';
import SpotifyProfilesView from './views/SpotifyProfilesView.vue';

const store = useSpotifyStore();
const { t, currentLanguage } = useI18n();


const audioLayoutRef = ref(null);
const layoutScrollRef = computed(() => audioLayoutRef.value?.scrollElement ?? null);
const { currentView, currentParams, currentKey, currentPage, keptKeys, pagesSettled, canGoBack, push, back, reset, goTo, pendingScrollRestore } =
  useNavigationStack('home', { scrollElRef: layoutScrollRef });

// The views that are one page of something (a list, an artist, its
// discography): an account switch leaves them.
const PAGES = ['context', 'artist', 'discography', 'section'];

const playback = useSourcePlaybackVisibility('spotify', {
  // What plays here, else what another device of the account plays.
  content: () => store.nowPlaying ?? store.remote,
});
const { shouldShowPlayer, displayed: nowPlaying } = playback;

const activeProfile = computed(() => store.profiles.find((p) => p.active) ?? null);

const currentTitle = computed(() => {
  if (currentView.value === 'profiles') return t('spotify.profiles');
  if (currentView.value === 'artist') return t('spotify.artist');
  if (currentView.value === 'discography') return t('spotify.discography');
  if (currentView.value === 'section') return currentParams.value.title || t('audioSources.spotify');
  if (currentView.value === 'context') {
    return {
      liked: t('spotify.likedSongs'),
      album: t('spotify.album'),
    }[currentParams.value.kind] ?? t('spotify.playlist');
  }
  return t('audioSources.spotify');
});

// === Navigation ===
// Each page's entry, from what opens it.
const albumPage = (album) => ({ uri: album.uri, kind: 'album', name: album.name || '', image: album.image || '' });
const artistPage = (artist) => ({ uri: artist.uri, name: artist.name || '', image: artist.image || '' });
const playlistPage = (playlist) =>
  ({ uri: playlist.uri, kind: 'playlist', name: playlist.name || '', image: playlist.image || '', owner: playlist.owner || '' });

// `from`: the key of the track row whose menu asked, which then waits on the
// page's first answer as a card does; the player's links open at once.
function openFrom(from, view, params) {
  if (from) opening.open({ uri: from }, view, params);
  else push(view, params);
}
function openAlbum(album, from) {
  if (album?.uri) openFrom(from, 'context', albumPage(album));
}
function openArtist(artist, from) {
  if (artist?.uri) openFrom(from, 'artist', artistPage(artist));
}
// A track's radio: the playlist Spotify made for it, named after the track
// (a nameless track leaves the page its untitled heading).
function openRadio({ uri, track }, from) {
  const name = track.title ? t('spotify.trackRadio', { title: track.title }) : '';
  openFrom(from, 'context', { uri, kind: 'playlist', name, image: track.artwork || '', owner: 'spotify' });
}
// A card or a tile, by the kind of page it opens. It waits on the card for the
// page's first answer; a page opened from the player opens at once on its own
// loading.
const opening = useSpotifyOpening(push);
function openItem(item) {
  if (item.kind === 'liked') {
    const uri = store.home?.liked_songs_uri;
    if (uri) opening.open(item, 'context', { uri, kind: 'liked' });
  } else if (item.kind === 'album') opening.open(item, 'context', albumPage(item));
  else if (item.kind === 'artist') opening.open(item, 'artist', artistPage(item));
  else opening.open(item, 'context', playlistPage(item));
}
function openPlayerAlbum() {
  const uri = nowPlaying.value?.albumUri;
  if (uri) openAlbum({ uri, name: nowPlaying.value.album });
}
// The artist line's names, each a link where Spotify names its page (an
// episode's show has none).
const playerArtists = computed(() =>
  (nowPlaying.value?.artists ?? []).map((artist) => ({ ...artist, link: !!artist.uri })));
// One of Spotify's sections whole (its "Show all"), under its own title.
function openSection(section) {
  push('section', { id: section.id, title: section.title || '', items: section.items });
}
function onScrollRestored() {
  pendingScrollRestore.value = null;
}

// A card left opening behind (back, another page, another account) opens
// nothing; nor does one whose artist page a language change dropped.
watch([currentKey, () => store.account, currentLanguage], opening.cancel);

// Another account's library: whatever page was open belonged to the last one.
watch(() => store.account, (now, before) => {
  if (now !== before && PAGES.includes(currentView.value)) reset();
});
</script>

<style scoped>
/* The height of a medium IconButton (icon + padding), so the avatar fills the
   header's actions row exactly as the buttons beside it do. */
.profile-button {
  --avatar-size: 48px;
  display: flex;
  /* Set apart from the buttons: --space-04 in all, over the row's gap. */
  margin-left: calc(var(--space-04) - var(--space-02));
  padding: 0;
  border: none;
  border-radius: var(--radius-full);
  background: transparent;
  cursor: pointer;
}

@media (max-aspect-ratio: 4/3) {
  .profile-button {
    --avatar-size: 38px;
  }
}
</style>
