<!-- LibrespotSource.vue - Version with automatic stagger -->
<template>
  <div class="librespot-player">
    <div class="now-playing-spotify">
      <!-- Left side: Cover image with CSS staggering -->
      <div class="album-art-section stagger-1">
        <div class="album-art-container">
          <!-- Background blur -->
          <div class="album-art-blur"
            :style="{ backgroundImage: persistentMetadata.album_art_url ? `url(${persistentMetadata.album_art_url})` : 'none' }">
          </div>

          <!-- Main cover art -->
          <div class="album-art">
            <img v-if="persistentMetadata.album_art_url" :src="persistentMetadata.album_art_url"
              alt="Album Art" />
          </div>
        </div>
      </div>

      <!-- Right side: Info and controls with CSS staggering -->
      <div class="content-section stagger-2">
        <!-- Block 1: Information (takes remaining space) -->
        <div class="track-info stagger-3">
          <h1 class="track-title heading-1">{{ persistentMetadata.title || 'Titre inconnu' }}</h1>
          <p class="track-artist heading-2">{{ persistentMetadata.artist || 'Artiste inconnu' }}</p>
        </div>

        <!-- Block 2: Controls (aligned at bottom) -->
        <div class="controls-section">
          <div class="progress-wrapper stagger-4">
            <ProgressBar :currentPosition="currentPosition" :duration="duration"
              :progressPercentage="progressPercentage" @seek="seekTo" />
          </div>
          <div class="controls-wrapper stagger-5">
            <PlaybackControls :isPlaying="isPlaying" @play-pause="togglePlayPause"
              @previous="previousTrack" @next="nextTrack" />
          </div>
        </div>
      </div>
    </div>

    <div v-if="unifiedStore.systemState.error && unifiedStore.systemState.active_source === 'librespot'" class="error-message">
      {{ unifiedStore.systemState.error }}
    </div>
  </div>
</template>

<script setup>
import { computed, watch, onMounted, ref } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useLibrespotControl } from '../librespot/useLibrespotControl';
import { usePlaybackProgress } from '../librespot/usePlaybackProgress';
import axios from 'axios';

import PlaybackControls from '../librespot/PlaybackControls.vue';
import ProgressBar from '../librespot/ProgressBar.vue';

const unifiedStore = useUnifiedAudioStore();
const { togglePlayPause, previousTrack, nextTrack } = useLibrespotControl();
const { currentPosition, duration, progressPercentage, seekTo } = usePlaybackProgress();

// === METADATA PERSISTENCE ===
const lastValidMetadata = ref({
  title: '',
  artist: '',
  album_art_url: ''
});

const persistentMetadata = computed(() => {
  const currentMetadata = unifiedStore.systemState.metadata || {};

  // If we currently have valid metadata, use and save it
  if (currentMetadata.title && currentMetadata.artist) {
    lastValidMetadata.value = {
      title: currentMetadata.title,
      artist: currentMetadata.artist,
      album_art_url: currentMetadata.album_art_url || ''
    };
    return lastValidMetadata.value;
  }

  // Otherwise, use the last saved valid metadata
  return lastValidMetadata.value;
});

// Real-time playback state (not persisted)
const isPlaying = computed(() => unifiedStore.systemState.metadata?.is_playing || false);

// === WATCHERS ===
watch(() => unifiedStore.systemState.metadata, (newMetadata) => {
  if (newMetadata?.position !== undefined) {
    // Synchronization is handled in usePlaybackProgress
  }
}, { immediate: true });

// === LIFECYCLE ===
onMounted(async () => {
  console.log('🎬 LibrespotView mounted - natural stagger');
  
  try {
    const response = await axios.get('/librespot/status');
    if (response.data.status === 'ok') {
      const metadata = response.data.metadata || {};

      unifiedStore.updateState({
        data: {
          full_state: {
            active_source: 'librespot',
            plugin_state: response.data.plugin_state,
            transitioning: false,
            metadata: metadata,
            error: null
          }
        }
      });

      console.log("Position initiale chargée:", metadata.position);
    }
  } catch (error) {
    console.error('Error fetching librespot status:', error);
  }
});
</script>

<style scoped>
/* === SIMPLE AND NATURAL STAGGERING === */

/* Initial states: all elements are hidden */
.stagger-1,
.stagger-2,
.stagger-3,
.stagger-4,
.stagger-5 {
  opacity: 0;
  transform: translateY(var(--space-07));
}

/* Animation with two separate effects */
.librespot-player .stagger-1,
.librespot-player .stagger-2,
.librespot-player .stagger-3,
.librespot-player .stagger-4,
.librespot-player .stagger-5 {
  animation: 
    stagger-transform var(--transition-spring) forwards,
    stagger-opacity 0.4s ease forwards;
}

/* Simple staggered delays */
.librespot-player .stagger-1 { animation-delay: 0ms; }
.librespot-player .stagger-2 { animation-delay: 0ms; }
.librespot-player .stagger-3 { animation-delay: 100ms; }
.librespot-player .stagger-4 { animation-delay: 200ms; }
.librespot-player .stagger-5 { animation-delay: 300ms; }

/* Spring animation for transform */
@keyframes stagger-transform {
  to {
    transform: translateY(0);
  }
}

/* Ease animation for opacity */
@keyframes stagger-opacity {
  to {
    opacity: 1;
  }
}

/* === COMPONENT STYLES === */
.librespot-player {
  width: 100%;
  height: 100%;
  overflow: hidden;
  /* Ensure parent transitions work */
  position: relative;
}

.now-playing-spotify {
  display: flex;
  height: 100%;
  padding: var(--space-05);
  gap: var(--space-06);
  background: var(--color-background-neutral);
}

/* Album Art */
.album-art-section {
  flex-shrink: 0;
  aspect-ratio: 1;
  order: 1;
  z-index: 2;
}

/* Content Section */
.content-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  order: 2;
  z-index: 1;
}

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
  filter: blur(var(--space-07)) saturate(1.5);
  transform: scale(1.1) translateZ(0);
  opacity: .25;
  will-change: transform, filter;
  backface-visibility: hidden;
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
  padding-top: var(--space-06);
}

.controls-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.track-title {
  color: var(--color-text);
}

.track-artist {
  color: var(--color-text-light);
}

.error-message {
  color: #ff4444;
  margin-top: 10px;
  text-align: center;
  padding: 10px;
  background-color: #fff0f0;
  border-radius: 4px;
}

@media (max-aspect-ratio: 4/3) {
  .now-playing-spotify {
    padding: var(--space-05) var(--space-05) 0 var(--space-05);
    flex-direction: column;
    gap: 0;
  }

  .controls-section {
    margin-bottom: var(--space-06);
  }

  .album-art-blur {
    transform: scale(1) translateZ(0);
  }

  .track-info {
    padding: var(--space-06) 0 var(--space-03) 0;
  }
}

/* iOS */
.ios-app .now-playing-spotify {
  padding: var(--space-08) var(--space-05) 0 var(--space-05);
}

.ios-app .controls-section {
  margin-bottom: var(--space-09);
}

/* Android */
.android-app .controls-section {
  margin-bottom: var(--space-08);
}
</style>