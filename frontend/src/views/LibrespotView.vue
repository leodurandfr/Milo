<template>
  <div class="librespot-player">
    <div v-if="hasTrackInfo" class="now-playing" :class="{ 'animate-in': showPlayer }">
      <!-- Partie gauche : Image de couverture -->
      <div class="album-art-section" :class="{ 'slide-in': showAlbumArt }">
        <div class="album-art">
          <img v-if="unifiedStore.metadata.album_art_url" :src="unifiedStore.metadata.album_art_url" alt="Album Art" />
          <div v-else class="placeholder-art">
            <span class="music-icon">🎵</span>
          </div>
        </div>
      </div>

      <!-- Partie droite : Informations et contrôles -->
      <div class="content-section">
        <!-- Bloc 1 : Informations (prend l'espace restant) -->
        <div class="track-info" :class="{ 'slide-up': showTrackInfo }">
          <h1 class="track-title heading-1">{{ unifiedStore.metadata.title || 'Titre inconnu' }}</h1>
          <p class="track-artist heading-2">{{ unifiedStore.metadata.artist || 'Artiste inconnu' }}</p>
        </div>

        <!-- Bloc 2 : Contrôles (aligné en bas) -->
        <div class="controls-section">
          <div class="progress-wrapper" :class="{ 'slide-up': showProgressBar }">
            <ProgressBar :currentPosition="currentPosition" :duration="duration"
              :progressPercentage="progressPercentage" @seek="seekToPosition" />
          </div>
          <div class="controls-wrapper" :class="{ 'slide-up': showControls }">
            <PlaybackControls :isPlaying="unifiedStore.metadata.is_playing" @play-pause="togglePlayPause"
              @previous="previousTrack" @next="nextTrack" />
          </div>
        </div>
      </div>
    </div>

    <div v-else-if="unifiedStore.pluginState === 'ready'" class="plugin-status-wrapper">
      <PluginStatus plugin-type="librespot" :plugin-state="unifiedStore.pluginState" />
    </div>

    <div v-if="unifiedStore.error && unifiedStore.currentSource === 'librespot'" class="error-message">
      {{ unifiedStore.error }}
    </div>
  </div>
</template>

<script setup>
import { computed, watch, onMounted, ref, nextTick } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useLibrespotControl } from '@/composables/useLibrespotControl';
import { usePlaybackProgress } from '@/composables/usePlaybackProgress';
import axios from 'axios';

import PlaybackControls from '../components/librespot/PlaybackControls.vue';
import ProgressBar from '../components/librespot/ProgressBar.vue';
import PluginStatus from '@/components/ui/PluginStatus.vue';

const unifiedStore = useUnifiedAudioStore();
const { togglePlayPause, previousTrack, nextTrack } = useLibrespotControl();
const { currentPosition, duration, progressPercentage, seekTo } = usePlaybackProgress();

// États d'animation
const showPlayer = ref(false);
const showAlbumArt = ref(false);
const showTrackInfo = ref(false);
const showProgressBar = ref(false);
const showControls = ref(false);

const hasTrackInfo = computed(() => {
  return !!(
    unifiedStore.currentSource === 'librespot' &&
    unifiedStore.pluginState === 'connected' &&
    unifiedStore.metadata?.title &&
    unifiedStore.metadata?.artist
  );
});

// Animation d'apparition du player
async function animatePlayerIn() {
  showPlayer.value = true;
  await nextTick();

  // Album art apparaît en premier
  showAlbumArt.value = true;

  // Track info après 200ms
  setTimeout(() => {
    showTrackInfo.value = true;
  }, 100);

  // Progress bar après 400ms
  setTimeout(() => {
    showProgressBar.value = true;
  }, 200);

  // Controls après 600ms
  setTimeout(() => {
    showControls.value = true;
  }, 300);
}

// Reset des animations
function resetAnimations() {
  showPlayer.value = false;
  showAlbumArt.value = false;
  showTrackInfo.value = false;
  showProgressBar.value = false;
  showControls.value = false;
}

// Watch pour déclencher l'animation
watch(() => hasTrackInfo.value, async (newValue, oldValue) => {
  if (newValue && !oldValue) {
    // Reset d'abord les animations
    resetAnimations();
    // Attendre le prochain tick pour que le DOM soit mis à jour
    await nextTick();
    // Petit délai pour s'assurer que PluginStatus a disparu
    setTimeout(() => {
      animatePlayerIn();
    }, 50);
  } else if (!newValue) {
    resetAnimations();
  }
}, { immediate: false });

function seekToPosition(position) {
  seekTo(position);
}

watch(() => unifiedStore.metadata, (newMetadata) => {
  if (newMetadata?.position !== undefined) {
    // La synchronisation est gérée dans usePlaybackProgress
  }
}, { immediate: true });

onMounted(async () => {
  if (unifiedStore.currentSource === 'librespot') {
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

        // Déclencher l'animation si on a déjà les infos
        if (hasTrackInfo.value) {
          await nextTick();
          setTimeout(() => {
            animatePlayerIn();
          }, 100);
        }
      }
    } catch (error) {
      console.error('Error fetching librespot status:', error);
    }
  }
});
</script>

<style scoped>
.librespot-player {
  width: 100%;
  height: 100%;
}

.now-playing {
  display: flex;
  height: 100%;
  padding: var(--space-05);
  gap: var(--space-06);
  background: var(--color-background-neutral);
}

.plugin-status-wrapper {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-05);
}

/* Animations */
.album-art-section {
  flex-shrink: 0;
  aspect-ratio: 1;
  opacity: 0;
  transform: translateY(20px);
  transition: opacity 0.6s cubic-bezier(0.25, 1, 0.5, 1), transform 0.6s cubic-bezier(0.25, 1, 0.5, 1);
}

.album-art-section.slide-in {
  opacity: 1;
  transform: translateY(0);
}

.content-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.track-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  text-align: center;
  gap: var(--space-04);
  padding: var(--space-06) 0;
  opacity: 0;
  transform: translateY(20px);
  transition: opacity 0.6s cubic-bezier(0.25, 1, 0.5, 1), transform 0.6s cubic-bezier(0.25, 1, 0.5, 1);
}

.track-info.slide-up {
  opacity: 1;
  transform: translateY(0);
}

.progress-wrapper {
  opacity: 0;
  transform: translateY(20px);
  transition: opacity 0.6s cubic-bezier(0.25, 1, 0.5, 1), transform 0.6s cubic-bezier(0.25, 1, 0.5, 1);
}

.progress-wrapper.slide-up {
  opacity: 1;
  transform: translateY(0);
}

.controls-wrapper {
  opacity: 0;
  transform: translateY(20px);
  transition: opacity 0.6s cubic-bezier(0.25, 1, 0.5, 1), transform 0.6s cubic-bezier(0.25, 1, 0.5, 1);
}

.controls-wrapper.slide-up {
  opacity: 1;
  transform: translateY(0);
}

.controls-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-07);
}

.album-art {
  width: 100%;
  height: 100%;
  border-radius: var(--radius-06);
  overflow: hidden;
}

.album-art img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.placeholder-art {
  width: 100%;
  height: 100%;
  background-color: #333;
  display: flex;
  align-items: center;
  justify-content: center;
}

.music-icon {
  font-size: 80px;
  opacity: 0.5;
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
  .now-playing {
    padding: var(--space-05) var(--space-05) 0 var(--space-05);
    flex-direction: column;
    gap: 0;
  }

  .controls-section {
    margin-bottom: var(--space-06);
  }
}

/* iOS */
.ios-app .now-playing {
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