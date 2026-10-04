<template>
  <!-- The navigation, and the full player it expands into: BrowserSourceViews
       swaps the two. -->
  <BrowserSourceViews source="spotify" :playback="playback"
    :can-open-album="!!nowPlaying?.albumUri" :can-open-artist="!!nowPlaying?.artistUri"
    @title-click="openPlayerAlbum" @secondary-click="openPlayerArtist">
    <template #navigation="{ bar }">
      <AudioSourceLayout ref="audioLayoutRef" :show-player="shouldShowPlayer"
        :header-title="currentTitle" :header-show-back="canGoBack" :header-title-muted="currentView === 'context'"
        header-icon="spotify" gradient="spotify"
        :header-actions-key="currentView" :content-key="contentKey"
        :player-mobile-height="144" :pending-scroll-restore="pendingScrollRestore"
        @header-back="back" @scroll-restored="onScrollRestored">

        <!-- Home only: whose library this is, and the way to the others. -->
        <template v-if="currentView === 'home' && activeProfile" #header-actions>
          <button v-press type="button" class="profile-button" :aria-label="t('spotify.profiles')"
            @click="goTo('profiles')">
            <ProfileAvatar :profile="activeProfile" :size="40" />
          </button>
        </template>

        <template #content>
          <SpotifyHome v-if="currentView === 'home'" key="home" @select="openItem" />

          <SpotifyProfilesView v-else-if="currentView === 'profiles'" key="profiles" @picked="reset" />

          <SpotifyContextView v-else-if="currentView === 'context'" :key="currentParams.uri"
            :uri="currentParams.uri" :kind="currentParams.kind" :name="currentParams.name"
            :image="currentParams.image" :owner="currentParams.owner"
            @select-artist="openArtist" @select-album="openAlbum" />
        </template>

        <!-- Docked player: it reads what it draws from the state (no queue
             carousel: go-librespot does not say what comes next); the album
             and the artist it emits open here, in the navigation. -->
        <template #player>
          <AudioPlayer v-bind="bar" source="spotify"
            @title-click="openPlayerAlbum" @secondary-click="openPlayerArtist" />
        </template>
      </AudioSourceLayout>
    </template>
  </BrowserSourceViews>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { useSpotifyStore } from '@/stores/spotifyStore';
import { useNavigationStack } from '@/composables/useNavigationStack';
import { useSourcePlaybackVisibility } from '@/composables/useSourcePlaybackVisibility';
import { useI18n } from '@/services/i18n';
import AudioPlayer from '@/components/audio/AudioPlayer.vue';
import BrowserSourceViews from '@/components/audio/BrowserSourceViews.vue';
import AudioSourceLayout from '@/components/audio/AudioSourceLayout.vue';
import ProfileAvatar from './ProfileAvatar.vue';
import SpotifyHome from './views/SpotifyHome.vue';
import SpotifyContextView from './views/SpotifyContextView.vue';
import SpotifyProfilesView from './views/SpotifyProfilesView.vue';

const store = useSpotifyStore();
const { t } = useI18n();


const audioLayoutRef = ref(null);
const layoutScrollRef = computed(() => audioLayoutRef.value?.scrollElement ?? null);
const { currentView, currentParams, canGoBack, push, back, reset, goTo, pendingScrollRestore } =
  useNavigationStack('home', { scrollElRef: layoutScrollRef });

// Two context pages in a row (an album, then its artist) are two contents.
const contentKey = computed(() =>
  currentView.value === 'context' ? `context:${currentParams.value.uri}` : currentView.value
);

const playback = useSourcePlaybackVisibility('spotify', {
  content: () => store.nowPlaying,
});
const { shouldShowPlayer, displayed: nowPlaying } = playback;

const activeProfile = computed(() => store.profiles.find((p) => p.active) ?? null);

const currentTitle = computed(() => {
  if (currentView.value === 'profiles') return t('spotify.profiles');
  if (currentView.value === 'context') {
    return {
      liked: t('spotify.likedSongs'),
      album: t('spotify.album'),
      artist: t('spotify.artist'),
    }[currentParams.value.kind] ?? t('spotify.playlist');
  }
  return t('audioSources.spotify');
});

// === Navigation ===
function openContext(params) {
  push('context', params);
}
function openPlaylist(playlist) {
  openContext({ uri: playlist.uri, kind: 'playlist', name: playlist.name || '', image: playlist.image || '', owner: playlist.owner || '' });
}
function openLiked() {
  const uri = store.home?.liked_songs_uri;
  if (uri) openContext({ uri, kind: 'liked' });
}
function openAlbum(album) {
  if (album?.uri) openContext({ uri: album.uri, kind: 'album', name: album.name || '', image: album.image || '' });
}
function openArtist(artist) {
  if (artist?.uri) openContext({ uri: artist.uri, kind: 'artist', name: artist.name || '', image: artist.image || '' });
}
// A home card or tile, by the kind of page it opens.
function openItem(item) {
  if (item.kind === 'liked') openLiked();
  else if (item.kind === 'album') openAlbum(item);
  else if (item.kind === 'artist') openArtist(item);
  else openPlaylist(item);
}
function openPlayerAlbum() {
  const uri = nowPlaying.value?.albumUri;
  if (uri) openAlbum({ uri, name: nowPlaying.value.album });
}
function openPlayerArtist() {
  const uri = nowPlaying.value?.artistUri;
  if (uri) openArtist({ uri, name: '' });
}
function onScrollRestored() {
  pendingScrollRestore.value = null;
}

// === Which screen opens ===
// Several profiles and nothing playing: the profile screen first, once per
// visit. Nobody signed in but profiles kept (one was just forgotten): the
// profile screen too, since home has nothing to list.
let gateChecked = false;
watch(() => store.profilesLoaded, (loaded) => {
  if (!loaded || gateChecked) return;
  gateChecked = true;
  if (store.opensOnProfiles || (!store.account && store.profiles.length > 0)) goTo('profiles');
}, { immediate: true });

// Another account's library: whatever page was open belonged to the last one.
watch(() => store.account, (now, before) => {
  if (now !== before && currentView.value === 'context') reset();
});

store.loadProfiles();
</script>

<style scoped>
.profile-button {
  display: flex;
  padding: 0;
  border: none;
  border-radius: var(--radius-full);
  background: transparent;
  cursor: pointer;
}
</style>
