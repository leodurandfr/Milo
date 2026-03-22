<!-- CDSource.vue - CD Player full-screen component -->
<template>
  <div class="cd-player">
    <!-- Empty state: no disc -->
    <div v-if="!cdStore.discPresent" class="cd-empty-state">
      <MessageContent icon="eject" :title="t('audioSources.cdSource.insertDisc')" />
    </div>

    <!-- Player content -->
    <div v-else class="now-playing" :class="{ 'tracklist-mode': cdStore.showTracklist }">
      <!-- Top-right action buttons -->
      <div class="action-buttons">
        <IconButton
          :icon="cdStore.showTracklist ? 'close' : 'queue'"
          variant="background-strong"
          size="small"
          @click="cdStore.toggleTracklist()"
        />
        <IconButton
          icon="eject"
          variant="background-strong"
          size="small"
          @click="cdStore.eject()"
        />
      </div>

      <!-- Album art section -->
      <div class="album-art-section stagger-1" :class="{ 'album-art-section--small': cdStore.showTracklist }">
        <div class="album-art-container">
          <div
            class="album-art-blur"
            :style="{ backgroundImage: coverUrl ? `url(${coverUrl})` : 'none' }"
          ></div>
          <div class="album-art">
            <img v-if="coverUrl" :src="coverUrl" alt="Album Art" />
          </div>
        </div>
      </div>

      <!-- Default mode: track info + controls -->
      <Transition name="cd-mode" mode="out-in">
        <div v-if="!cdStore.showTracklist" key="player" class="content-section">
          <!-- Track info -->
          <div class="track-info stagger-3">
            <h1 class="track-title heading-1">{{ trackTitle }}</h1>
            <p class="track-artist heading-2">{{ artistAlbum }}</p>
          </div>

          <!-- Controls -->
          <div class="controls-section">
            <div class="progress-wrapper stagger-4">
              <ConnectProgressBar
                :currentPosition="cdStore.positionMs"
                :duration="cdStore.durationMs"
                :progressPercentage="cdStore.progressPercentage"
                :isReady="cdStore.currentTrack !== null"
                :interactive="true"
                @seek="onSeek"
              />
            </div>
            <div class="controls-wrapper stagger-5">
              <PlaybackControls
                :isPlaying="cdStore.isPlaying && !cdStore.albumFinished"
                @play-pause="cdStore.togglePlayPause()"
                @previous="cdStore.prevTrack()"
                @next="cdStore.nextTrack()"
              />
            </div>
          </div>
        </div>

        <!-- Tracklist mode -->
        <div v-else key="tracklist" class="tracklist-section">
          <div class="tracklist-header">
            <h2 class="heading-2">{{ albumTitle }}</h2>
            <p class="text-mono-small tracklist-subtitle">{{ artistName }}</p>
          </div>
          <div class="tracklist-scroll">
            <TrackCard
              v-for="track in cdStore.tracks"
              :key="track.number"
              :track="track"
              :isCurrent="track.number === cdStore.currentTrack"
              :isPlaying="track.number === cdStore.currentTrack && cdStore.isPlaying"
              @play="cdStore.playTrack($event)"
            />
          </div>
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useCdStore } from '@/stores/cdStore';

import PlaybackControls from '@/components/audio/PlaybackControls.vue';
import ConnectProgressBar from '@/components/audio/ConnectProgressBar.vue';
import IconButton from '@/components/ui/IconButton.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import TrackCard from './TrackCard.vue';

const { t } = useI18n();
const cdStore = useCdStore();

// Cover art URL
const coverUrl = computed(() => cdStore.discInfo?.cover_url || '');

// Display text
const trackTitle = computed(() => {
  if (cdStore.currentTrackInfo?.title) return cdStore.currentTrackInfo.title;
  if (cdStore.currentTrack) return t('audioSources.cdSource.trackN', { n: cdStore.currentTrack });
  return cdStore.discInfo?.album || t('audioSources.cdSource.unknownAlbum');
});

const artistName = computed(() =>
  cdStore.discInfo?.artist || t('audioSources.cdSource.unknownArtist')
);

const albumTitle = computed(() =>
  cdStore.discInfo?.album || t('audioSources.cdSource.unknownAlbum')
);

const artistAlbum = computed(() => {
  const artist = artistName.value;
  const album = albumTitle.value;
  if (artist && album) return `${artist} — ${album}`;
  return artist || album;
});

// Seek handler: ConnectProgressBar emits position in ms, CD backend expects seconds
function onSeek(positionMs) {
  cdStore.seek(positionMs / 1000);
}
</script>

<style scoped>
/* === STAGGER ANIMATION === */
.stagger-1,
.stagger-3,
.stagger-4,
.stagger-5 {
  opacity: 0;
  transform: translateY(var(--space-07));
}

.cd-player .stagger-1,
.cd-player .stagger-3,
.cd-player .stagger-4,
.cd-player .stagger-5 {
  animation:
    stagger-transform var(--transition-spring) forwards,
    stagger-opacity 0.4s ease forwards;
}

.cd-player .stagger-1 { animation-delay: 0ms; }
.cd-player .stagger-3 { animation-delay: 100ms; }
.cd-player .stagger-4 { animation-delay: 200ms; }
.cd-player .stagger-5 { animation-delay: 300ms; }

@keyframes stagger-transform {
  to { transform: translateY(0); }
}

@keyframes stagger-opacity {
  to { opacity: 1; }
}

/* === LAYOUT === */
.cd-player {
  width: 100%;
  height: 100%;
  overflow: hidden;
  position: relative;
}

.cd-empty-state {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-05);
}

.now-playing {
  display: flex;
  height: 100%;
  padding: var(--space-05) var(--space-06) var(--space-05) var(--space-05);
  gap: var(--space-06);
  background: var(--color-background-neutral);
  position: relative;
}

/* === ACTION BUTTONS === */
.action-buttons {
  position: absolute;
  top: var(--space-04);
  right: var(--space-04);
  display: flex;
  gap: var(--space-02);
  z-index: 10;
}

/* === ALBUM ART === */
.album-art-section {
  flex-shrink: 0;
  aspect-ratio: 1;
  order: 1;
  z-index: 2;
  transition: all var(--transition-spring);
}

.album-art-section--small {
  width: 40%;
  aspect-ratio: 1;
}

.album-art-container {
  position: relative;
  width: 100%;
  height: 100%;
}

.album-art-blur {
  position: absolute;
  top: -20px;
  left: -20px;
  right: -20px;
  bottom: -20px;
  z-index: 2;
  background-size: cover;
  background-position: center;
  filter: blur(var(--blur-04)) saturate(1.5);
  transform: scale(1.1) translateZ(0);
  opacity: .25;
  will-change: transform;
  -webkit-backface-visibility: hidden;
  backface-visibility: hidden;
  contain: strict;
}

.album-art {
  position: relative;
  z-index: 3;
  width: 100%;
  height: 100%;
  border-radius: var(--radius-07);
  overflow: hidden;
  box-shadow: 0px 0px 96px 0px #0000000d;
  pointer-events: none;
}

.album-art img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* === CONTENT SECTION (Player mode) === */
.content-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  order: 2;
  z-index: 1;
  min-width: 0;
}

.track-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  text-align: center;
  gap: var(--space-03);
  padding-top: var(--space-06);
}

.track-title {
  color: var(--color-text);
}

.track-artist {
  color: var(--color-text-light);
}

.controls-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

/* === TRACKLIST SECTION === */
.tracklist-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  order: 2;
  z-index: 1;
  min-width: 0;
  overflow: hidden;
}

.tracklist-header {
  text-align: center;
  padding: var(--space-03) 0;
  flex-shrink: 0;
}

.tracklist-subtitle {
  color: var(--color-text-light);
}

.tracklist-scroll {
  flex: 1;
  overflow-y: auto;
  padding-bottom: var(--space-05);
}

/* === MODE TRANSITION === */
.cd-mode-enter-active {
  transition: opacity var(--transition-normal);
}

.cd-mode-leave-active {
  transition: opacity var(--transition-fast-leave);
}

.cd-mode-enter-from,
.cd-mode-leave-to {
  opacity: 0;
}

/* === RESPONSIVE (Portrait / Mobile) === */
@media (max-aspect-ratio: 4/3) {
  .now-playing {
    flex-direction: column;
    padding: var(--space-05);
    gap: 0;
  }

  .album-art-section {
    width: 100%;
    max-height: 40%;
  }

  .album-art-section--small {
    max-height: 25%;
    width: auto;
    align-self: center;
  }

  .album-art-blur {
    transform: scale(1) translateZ(0);
  }

  .track-info {
    padding: var(--space-04) 0 var(--space-03) 0;
  }

  .controls-section {
    margin-bottom: calc(env(safe-area-inset-bottom, 0px));
  }

  .tracklist-section {
    padding-top: var(--space-03);
  }

  .action-buttons {
    top: var(--space-03);
    right: var(--space-03);
  }
}
</style>
