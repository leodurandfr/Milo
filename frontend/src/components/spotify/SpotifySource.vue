<template>
  <div class="spotify-source">
    <AudioSourceLayout ref="audioLayoutRef" :show-player="shouldShowPlayer"
      :header-title="currentTitle" :header-show-back="canGoBack" :header-title-muted="currentView === 'context'"
      header-icon="spotify" header-variant="background-neutral" gradient="spotify"
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
        <SpotifyHome v-if="currentView === 'home'" key="home"
          @select-playlist="openPlaylist" @select-liked="openLiked" />

        <SpotifyProfilesView v-else-if="currentView === 'profiles'" key="profiles" @picked="reset" />

        <SpotifyContextView v-else-if="currentView === 'context'" :key="currentParams.uri"
          :uri="currentParams.uri" :kind="currentParams.kind" :name="currentParams.name"
          :image="currentParams.image" :owner="currentParams.owner"
          @select-artist="openArtist" @select-album="openAlbum" />
      </template>

      <!-- Docked player: Music Library's track player, without the queue
           carousel (go-librespot does not say what comes next). -->
      <template #player>
        <AudioPlayer :visible="shouldShowPlayer" source="spotify" @after-hide="onAfterHide"
          :artwork="playerArtwork" :title="playerTitle" swipe-enabled
          @swipe-next="store.next()" @swipe-prev="store.previous()"
          @artwork-click="openPlayerAlbum" @secondary-click="openPlayerArtist">
          <template #info>
            <PlayerInfoText class="vertical-layout" :title="playerTitle" :secondary="playerArtist" />
          </template>

          <template #progress>
            <div @click.stop>
              <ProgressBar :current-position="positionMs" :duration="durationMs"
                :progress-percentage="livePercent" :interactive="store.canSend('seek')"
                variant="dark" @seek="seekTo" />
            </div>
          </template>

          <template #controls>
            <div class="track-controls" @click.stop>
              <div class="playback-controls">
                <IconButton icon="shuffle" variant="ghost" size="small" class="track-transport-extra transport-secondary-round"
                  :aria-label="t('spotify.shuffle')"
                  :color="store.shuffle ? 'var(--color-text-contrast)' : 'var(--color-text-contrast-50)'"
                  :disabled="!store.canSend('set_shuffle')" @click="store.toggleShuffle()" />
                <div class="track-transport-main">
                  <IconButton icon="previous" variant="ghost" size="small" class="track-transport-extra transport-secondary"
                    :disabled="!store.canSend('prev')" @click="store.previous()" />
                  <IconButton :icon="pausesOnPress(store.phase) ? 'pause' : 'play'" variant="ghost" size="medium"
                    class="transport-primary" :loading="isBuffering" @click="togglePlayPause" />
                  <IconButton icon="next" variant="ghost" size="small" class="track-transport-extra transport-secondary"
                    :disabled="!store.canSend('next')" @click="store.next()" />
                </div>
                <IconButton :icon="store.repeat === 'track' ? 'repeatOnce' : 'repeat'" variant="ghost" size="small"
                  class="track-transport-extra transport-secondary-round" :aria-label="repeatLabel"
                  :color="store.repeat === 'off' ? 'var(--color-text-contrast-50)' : 'var(--color-text-contrast)'"
                  :disabled="!store.canSend('set_repeat')" @click="store.cycleRepeat()" />
                <IconButton :icon="store.currentLiked ? 'heart' : 'heartOff'" variant="ghost" size="small"
                  :aria-label="store.currentLiked ? t('spotify.unlike') : t('spotify.like')"
                  :color="store.currentLiked ? 'var(--color-text-contrast)' : 'var(--color-text-contrast-50)'"
                  class="track-transport-extra transport-secondary-round" @click="store.toggleCurrentLike()" />
              </div>
            </div>
          </template>
        </AudioPlayer>
      </template>
    </AudioSourceLayout>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { useSpotifyStore } from '@/stores/spotifyStore';
import { useNavigationStack } from '@/composables/useNavigationStack';
import { useSourcePlaybackVisibility } from '@/composables/useSourcePlaybackVisibility';
import { useSourceProgress } from '@/composables/useSourceProgress';
import { pausesOnPress } from '@/utils/transport';
import { useI18n } from '@/services/i18n';
import IconButton from '@/components/ui/IconButton.vue';
import AudioPlayer from '@/components/audio/AudioPlayer.vue';
import AudioSourceLayout from '@/components/audio/AudioSourceLayout.vue';
import PlayerInfoText from '@/components/audio/PlayerInfoText.vue';
import ProgressBar from '@/components/audio/ProgressBar.vue';
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

const {
  isBuffering, shouldShowPlayer,
  displayed: nowPlaying, onAfterHide,
} = useSourcePlaybackVisibility('spotify', {
  content: () => store.nowPlaying,
});

const { duration: durationMs, currentPosition: positionMs, progressPercentage: livePercent, seekTo } =
  useSourceProgress('spotify');

const playerTitle = computed(() => nowPlaying.value?.title || '');
const playerArtist = computed(() => nowPlaying.value?.artist || '');
const playerArtwork = computed(() => nowPlaying.value?.artwork || null);

const activeProfile = computed(() => store.profiles.find((p) => p.active) ?? null);

const repeatLabel = computed(() => ({
  off: t('spotify.repeatOff'),
  context: t('spotify.repeatContext'),
  track: t('spotify.repeatTrack'),
}[store.repeat]));

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
  if (album?.uri) openContext({ uri: album.uri, kind: 'album', name: album.name || '' });
}
function openArtist(artist) {
  if (artist?.uri) openContext({ uri: artist.uri, kind: 'artist', name: artist.name || '' });
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

// === Player controls ===
function togglePlayPause() {
  const running = pausesOnPress(store.phase);
  if (running && store.canSend('pause')) store.pause();
  else if (!running && store.canSend('resume')) store.resume();
}
</script>

<style scoped>
.spotify-source {
  width: 100%;
  height: 100%;
}

.profile-button {
  display: flex;
  padding: 0;
  border: none;
  border-radius: var(--radius-full);
  background: transparent;
  cursor: pointer;
}

/* The .track-controls row's layout lives in AudioPlayer.vue (:deep), as for
   Music Library: the gallery's SourceStage re-authors the same row. */
</style>
