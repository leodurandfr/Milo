<!-- AudioPlayerFull.vue - Full-screen player for Spotify, AirPlay, and CD -->
<template>
  <div class="connect-player">
    <div class="now-playing">
      <!-- Left side: Cover image with CSS staggering -->
      <div class="album-art-section stagger-1" :class="{ 'art-collapsed': hideContent }">
        <div class="album-art-container">
          <!-- Background blur -->
          <div class="album-art-blur"
            :style="{ backgroundImage: artworkUrl ? `url(${artworkUrl})` : 'none' }">
          </div>

          <!-- Main cover art -->
          <div class="album-art">
            <img v-if="artworkUrl" :src="artworkUrl"
              alt="Album Art" />
          </div>
        </div>
      </div>

      <!-- Right side: Info and controls with CSS staggering -->
      <div class="content-section stagger-2">
        <!-- Action buttons (used by CD for eject/tracklist) -->
        <slot name="action-buttons" />

        <!-- Content: player info or replacement (e.g., CD tracklist) -->
        <Transition name="player-swap" mode="out-in">
          <div v-if="!hideContent" key="player-info" class="player-info">
            <div class="track-info" :class="{ 'no-controls': !showControls }">
              <h1 class="track-title heading-1">{{ persistentMetadata.title || t('status.unknownTitle') }}</h1>
              <p class="track-artist heading-2">{{ persistentMetadata.artist || t('status.unknownArtist') }}</p>
            </div>
            <div class="controls-section">
              <template v-if="showControls">
                <div class="progress-wrapper">
                  <ConnectProgressBar :currentPosition="currentPosition" :duration="duration"
                    :progressPercentage="progressPercentage" :isReady="isPositionInitialized"
                    :interactive="true" @seek="seekTo" />
                </div>
                <div class="controls-wrapper">
                  <PlaybackControls :isPlaying="isPlaying"
                    @play-pause="togglePlayPause" @previous="previousTrack" @next="nextTrack" />
                </div>
              </template>
              <div v-else-if="clientName" class="source-bar">
                <AppIcon :name="source" :size="40" />
                <span class="source-bar-name heading-4">{{ clientName }}</span>
              </div>
            </div>
          </div>
          <div v-else key="content-replace" class="content-replace">
            <slot name="content-replace" />
          </div>
        </Transition>
      </div>
    </div>

    <div v-if="unifiedStore.systemState.error && unifiedStore.systemState.active_source === source" class="error-message">
      {{ unifiedStore.systemState.error }}
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useSourceProgress } from '@/composables/useSourceProgress';
import { useI18n } from '@/services/i18n';
import { logger } from '@/services/logger';

import PlaybackControls from './PlaybackControls.vue';
import ConnectProgressBar from './ConnectProgressBar.vue';
import AppIcon from '@/components/ui/AppIcon.vue';
import cdPlaceholder from '@/assets/cd/cd-placeholder.jpg';

const props = defineProps({
  source: {
    type: String,
    required: true
  },
  showControls: {
    type: Boolean,
    default: true
  },
  hideContent: {
    type: Boolean,
    default: false
  }
});

const { t } = useI18n();
const unifiedStore = useUnifiedAudioStore();
const { currentPosition, duration, progressPercentage, seekTo, isPositionInitialized } = useSourceProgress(props.source);

// Playback controls
async function sendSourceCommand(command) {
  try {
    await unifiedStore.sendCommand(props.source, command);
  } catch (error) {
    logger.error('component', `Error executing command ${command} on ${props.source}`, error);
  }
}

function togglePlayPause() {
  sendSourceCommand(unifiedStore.systemState.metadata?.is_playing ? 'pause' : 'resume');
}

function previousTrack() {
  sendSourceCommand('prev');
}

function nextTrack() {
  sendSourceCommand('next');
}

// === METADATA PERSISTENCE ===
const lastValidMetadata = ref({
  title: '',
  artist: '',
  album_art_url: ''
});

// Cache last valid metadata so the UI doesn't blank out during brief gaps
watch(
  () => unifiedStore.systemState.metadata,
  (currentMetadata) => {
    const meta = currentMetadata || {};
    if (meta.title && meta.artist) {
      lastValidMetadata.value = {
        title: meta.title,
        artist: meta.artist,
        album_art_url: meta.album_art_url || ''
      };
    }
  },
  { immediate: true }
);

const persistentMetadata = computed(() => lastValidMetadata.value);

// Real-time playback state (not persisted)
const isPlaying = computed(() => unifiedStore.systemState.metadata?.is_playing || false);


// Client/device name (for source bar when controls are hidden)
const clientName = computed(() => unifiedStore.systemState.metadata?.client_name || '');

// Artwork URL with source-specific placeholder fallback
const placeholders = { cd: cdPlaceholder };
const artworkUrl = computed(() => persistentMetadata.value.album_art_url || placeholders[props.source] || '');



</script>

<style scoped>
/* === SIMPLE AND NATURAL STAGGERING === */

/* Initial states: all elements are hidden */
.stagger-1,
.stagger-2 {
  opacity: 0;
  transform: translateY(var(--space-07));
}

/* Animation with two separate effects */
.connect-player .stagger-1,
.connect-player .stagger-2 {
  animation:
    stagger-transform var(--transition-spring) forwards,
    stagger-opacity 0.4s ease forwards;
}

/* Simple staggered delays */
.connect-player .stagger-1 { animation-delay: 0ms; }
.connect-player .stagger-2 { animation-delay: 0ms; }

/* Spring animation for transform */
@keyframes stagger-transform {
  to {
    transform: none;
  }
}

/* Ease animation for opacity */
@keyframes stagger-opacity {
  to {
    opacity: 1;
  }
}

/* === COMPONENT STYLES === */
.connect-player {
  width: 100%;
  height: 100%;
  overflow: hidden;
  position: relative;
}

.now-playing {
  display: flex;
  height: 100%;
  padding: var(--space-05) var(--space-06) var(--space-05) var(--space-05);
  gap: var(--space-06);
  background: var(--color-background-neutral);
}

/* Album Art */
.album-art-section {
  flex-shrink: 0;
  aspect-ratio: 1;
  order: 1;
  z-index: 2;
  pointer-events: none;
}

/* Content Section */
.content-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  order: 2;
  z-index: 1;
}

/* Player info (track-info + controls) */
.player-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  min-width: 0;
  min-height: 0;
}

/* Content replacement (e.g., CD tracklist) */
.content-replace {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

/* === PLAYER SWAP TRANSITION === */
/* Leave: quick fade out */
.player-swap-leave-active {
  transition: opacity var(--transition-fast-leave);
}

.player-swap-leave-to {
  opacity: 0;
}

/* Enter: no parent animation — children stagger themselves */

/* Stagger children on mount (initial load + re-enter after swap) */
.player-info > .track-info,
.player-info > .controls-section {
  opacity: 0;
  transform: translateY(var(--space-05));
  animation:
    stagger-transform var(--transition-spring) forwards,
    stagger-opacity 0.4s ease forwards;
}

.player-info > .track-info { animation-delay: 0ms; }
.player-info > .controls-section { animation-delay: 100ms; }

/* Container for the two stacked cover arts */
.album-art-container {
  position: relative;
  width: 100%;
  height: 100%;
}

/* Background cover art with blur */
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

/* Main cover art with border radius */
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

.track-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  text-align: center;
  gap: var(--space-03);
  min-width: 0;
  padding-top: var(--space-06);
}

.track-info.no-controls {
  padding-top: 0;
}

.controls-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.track-title {
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.track-artist {
  color: var(--color-text-light);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Source bar (AirPlay device info) */
.source-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-03);
  padding-bottom: var(--space-06);
}

.source-bar-name {
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.error-message {
  color: var(--color-error);
  margin-top: var(--space-03);
  text-align: center;
  padding: var(--space-03);
  background-color: var(--color-background-strong);
  border-radius: var(--radius-01);
}

@media (max-aspect-ratio: 4/3) {
  .now-playing {
    padding-left: var(--space-05);
    padding-right: var(--space-05);
    padding-top: max(var(--space-05), env(safe-area-inset-top, 0px));
    padding-bottom: max(var(--space-06), env(safe-area-inset-bottom, 0px));

    flex-direction: column;
    gap: 0;
  }

  .content-replace {
    margin-bottom: calc(-1 * max(var(--space-06), env(safe-area-inset-bottom, 0px)));
  }

  .controls-section {
    margin-bottom: calc(env(safe-area-inset-bottom, 0px));
  }

  .content-section {
    z-index: auto;
  }

  .connect-player .content-section {
    transform: none;
    opacity: 1;
    animation: none;
  }

  /* Collapse album art when tracklist is open, keeping a strip for action buttons */
  .album-art-section {
    transition: margin-top 400ms var(--easeInOutCubic);
  }

  .album-art-section.art-collapsed {
    /* Buttons absolute top (from connect-player) minus album-art offset (from now-playing padding) */
    --btn-top: calc(max(var(--space-05), env(safe-area-inset-top, 0px)) + var(--space-04));
    --art-top: max(var(--space-05), env(safe-area-inset-top, 0px));
    --btn-height: 40px;
    --art-visible: calc(var(--btn-top) - var(--art-top) + var(--btn-height) + var(--space-04));
    margin-top: calc(-100vw + 2 * var(--space-05) + var(--art-visible));
  }

  .album-art-blur {
    transform: scale(1) translateZ(0);
  }

  .track-info {
    padding: var(--space-06) 0 var(--space-03) 0;
  }

  .track-info.no-controls {
    padding: 0;
  }
}
</style>
