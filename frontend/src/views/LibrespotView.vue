<template>
  <div class="librespot-player">
    <div v-if="hasTrackInfo" class="now-playing">
      <!-- Partie gauche : Image de couverture -->
      <div class="album-art-section" :class="{ 'slide-in': showAlbumArt }">
        <div class="album-art-container">
          <!-- Blur en arrière-plan -->
          <div class="album-art-blur"
            :style="{ backgroundImage: unifiedStore.metadata.album_art_url ? `url(${unifiedStore.metadata.album_art_url})` : 'none' }">
          </div>

          <!-- Art cover principale -->
          <div class="album-art">
            <img v-if="unifiedStore.metadata.album_art_url" :src="unifiedStore.metadata.album_art_url"
              alt="Album Art" />
          </div>
        </div>
      </div>

      <!-- Partie droite : Informations et contrôles -->
      <div class="content-section" :class="{ 'slide-in': showContentSection }">
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

// Props OPTIM : Juste shouldAnimate
const props = defineProps({
  shouldAnimate: {
    type: Boolean,
    default: false
  }
});

const unifiedStore = useUnifiedAudioStore();
const { togglePlayPause, previousTrack, nextTrack } = useLibrespotControl();
const { currentPosition, duration, progressPercentage, seekTo } = usePlaybackProgress();

// États d'animation
const showAlbumArt = ref(false);
const showContentSection = ref(false);
const showTrackInfo = ref(false);
const showProgressBar = ref(false);
const showControls = ref(false);
const hasAnimated = ref(false);

const hasTrackInfo = computed(() => {
  return !!(
    unifiedStore.pluginState === 'connected' &&
    unifiedStore.metadata?.title &&
    unifiedStore.metadata?.artist
  );
});

// === ANIMATION OPTIM ===

async function animateIn() {
  console.log('🎬 LibrespotView: Animating in');
  hasAnimated.value = true;
  
  // Forcer le DOM à être prêt
  await nextTick();
  await new Promise(resolve => {
    requestAnimationFrame(() => {
      requestAnimationFrame(resolve);
    });
  });

  // Album art et content section ensemble
  showAlbumArt.value = true;
  showContentSection.value = true;

  // Stagger des infos
  setTimeout(() => showTrackInfo.value = true, 100);
  setTimeout(() => showProgressBar.value = true, 200);
  setTimeout(() => showControls.value = true, 300);
}

function animateOut() {
  console.log('🎬 LibrespotView: Animating out');
  showControls.value = false;
  showProgressBar.value = false;
  showTrackInfo.value = false;
  
  setTimeout(() => {
    showAlbumArt.value = false;
    showContentSection.value = false;
    hasAnimated.value = false;
  }, 100);
}

// === WATCHER PRINCIPAL OPTIM ===

watch(() => props.shouldAnimate, async (shouldAnim) => {
  console.log('🎵 LibrespotView shouldAnimate:', shouldAnim);
  
  if (shouldAnim && hasTrackInfo.value && !hasAnimated.value) {
    await animateIn();
  } else if (!shouldAnim && hasAnimated.value) {
    animateOut();
  }
}, { immediate: true });

// === FONCTIONS UTILITAIRES ===

function seekToPosition(position) {
  seekTo(position);
}

watch(() => unifiedStore.metadata, (newMetadata) => {
  if (newMetadata?.position !== undefined) {
    // La synchronisation est gérée dans usePlaybackProgress
  }
}, { immediate: true });

onMounted(async () => {
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
.librespot-player {
  width: 100%;
  height: 100%;
  overflow: hidden;
}

.now-playing {
  display: flex;
  height: 100%;
  padding: var(--space-05);
  gap: var(--space-06);
  background: var(--color-background-neutral);
}

/* Album Art - FORCER l'état initial */
.album-art-section {
  flex-shrink: 0;
  aspect-ratio: 1;
  opacity: 0 !important;
  transform: translateY(20px) !important;
  transition: opacity 0.6s cubic-bezier(0.25, 1, 0.5, 1), transform 0.6s cubic-bezier(0.25, 1, 0.5, 1);
  order: 1;
  z-index: 2;
}

.album-art-section.slide-in {
  opacity: 1 !important;
  transform: translateY(0) !important;
}

/* Content Section - FORCER l'état initial */
.content-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  order: 2;
  z-index: 1;
  opacity: 0 !important;
  transform: translateY(20px) !important;
  transition: opacity 0.6s cubic-bezier(0.25, 1, 0.5, 1), transform 0.6s cubic-bezier(0.25, 1, 0.5, 1);
}

.content-section.slide-in {
  opacity: 1 !important;
  transform: translateY(0) !important;
}

/* Container pour les deux art covers superposées */
.album-art-container {
  position: relative;
  width: 100%;
  height: 100%;
}

/* Art cover en arrière-plan avec blur */
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

/* Art cover principale avec border radius */
.album-art {
  position: relative;
  z-index: 3;
  width: 100%;
  height: 100%;
  border-radius: var(--radius-06);
  overflow: hidden;
  box-shadow: 0px 0px 96px 0px #0000000d;
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
  .now-playing {
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