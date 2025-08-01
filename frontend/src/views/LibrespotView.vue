<!-- LibrespotView.vue - Version animation unique avec preload -->
<template>
  <div v-if="isReadyToShow" class="librespot-player" :class="{ 'animate-stagger': shouldAnimate }">
    <div class="now-playing">
      <!-- Partie gauche : Image de couverture avec staggering CSS -->
      <div class="album-art-section stagger-1">
        <div class="album-art-container">
          <!-- Blur en arrière-plan -->
          <div class="album-art-blur"
            :style="{ backgroundImage: persistentMetadata.album_art_url ? `url(${persistentMetadata.album_art_url})` : 'none' }">
          </div>

          <!-- Art cover principale -->
          <div class="album-art">
            <img v-if="persistentMetadata.album_art_url" :src="persistentMetadata.album_art_url"
              alt="Album Art" />
          </div>
        </div>
      </div>

      <!-- Partie droite : Informations et contrôles avec staggering CSS -->
      <div class="content-section stagger-2">
        <!-- Bloc 1 : Informations (prend l'espace restant) -->
        <div class="track-info stagger-3">
          <h1 class="track-title heading-1">{{ persistentMetadata.title || 'Titre inconnu' }}</h1>
          <p class="track-artist heading-2">{{ persistentMetadata.artist || 'Artiste inconnu' }}</p>
        </div>

        <!-- Bloc 2 : Contrôles (aligné en bas) -->
        <div class="controls-section">
          <div class="progress-wrapper stagger-4">
          <ProgressBar :currentPosition="currentPosition" :duration="duration"
            :progressPercentage="progressPercentage" @seek="seekTo" />
          </div>
          <div class="controls-wrapper stagger-5">
            <PlaybackControls :isPlaying="persistentMetadata.is_playing" @play-pause="togglePlayPause"
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
import { computed, watch, onMounted, ref } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useLibrespotControl } from '@/composables/useLibrespotControl';
import { usePlaybackProgress } from '@/composables/usePlaybackProgress';
import axios from 'axios';

import PlaybackControls from '../components/librespot/PlaybackControls.vue';
import ProgressBar from '../components/librespot/ProgressBar.vue';

const unifiedStore = useUnifiedAudioStore();
const { togglePlayPause, previousTrack, nextTrack } = useLibrespotControl();
const { currentPosition, duration, progressPercentage, seekTo } = usePlaybackProgress();

// === LOGIQUE SIMPLE D'ANIMATION UNIQUE ===
const hasAnimated = ref(false);
const imageLoaded = ref(false);
const imageError = ref(false);

// === PERSISTANCE DES MÉTADONNÉES ===
const lastValidMetadata = ref({
  title: '',
  artist: '',
  album_art_url: '',
  is_playing: false
});

const persistentMetadata = computed(() => {
  const currentMetadata = unifiedStore.metadata || {};
  
  // Si on a des métadonnées valides actuellement, les utiliser et les sauvegarder
  if (currentMetadata.title && currentMetadata.artist) {
    lastValidMetadata.value = {
      title: currentMetadata.title,
      artist: currentMetadata.artist,
      album_art_url: currentMetadata.album_art_url || '',
      is_playing: currentMetadata.is_playing || false
    };
    return lastValidMetadata.value;
  }
  
  // Sinon, utiliser les dernières métadonnées valides sauvegardées
  return lastValidMetadata.value;
});

// === LOGIQUE D'AFFICHAGE SIMPLE ===
const isReadyToShow = computed(() => {
  // Si on a déjà animé, toujours afficher
  if (hasAnimated.value) return true;
  
  // Première fois : attendre le preload de l'image si elle existe
  if (persistentMetadata.value.album_art_url) {
    return imageLoaded.value || imageError.value;
  }
  
  // Pas d'image : afficher directement
  return true;
});

const shouldAnimate = computed(() => {
  // Animer seulement si on n'a pas encore animé ET qu'on est prêt à afficher
  return !hasAnimated.value && isReadyToShow.value;
});

// === PRELOAD DE L'IMAGE (seulement si pas encore animé) ===
watch(() => persistentMetadata.value.album_art_url, (newUrl) => {
  // Si on a déjà animé, pas de preload nécessaire
  if (hasAnimated.value) return;
  
  if (!newUrl) {
    imageLoaded.value = true;
    imageError.value = false;
    return;
  }
  
  // Reset des états
  imageLoaded.value = false;
  imageError.value = false;
  
  // Preload de l'image
  const img = new Image();
  img.onload = () => {
    imageLoaded.value = true;
    console.log('🖼️ Image preloaded:', newUrl);
  };
  img.onerror = () => {
    imageError.value = true;
    console.warn('🖼️ Image preload failed:', newUrl);
  };
  img.src = newUrl;
}, { immediate: true });

// === MARQUER COMME ANIMÉ APRÈS LA PREMIÈRE ANIMATION ===
watch(shouldAnimate, (newValue) => {
  if (newValue) {
    // Une fois qu'on commence à animer, marquer comme animé après un délai
    setTimeout(() => {
      hasAnimated.value = true;
      console.log('🎬 LibrespotView animation completed');
    }, 600); // Durée de l'animation stagger la plus longue
  }
});

// === WATCHERS ===
watch(() => unifiedStore.metadata, (newMetadata) => {
  if (newMetadata?.position !== undefined) {
    // La synchronisation est gérée dans usePlaybackProgress
  }
}, { immediate: true });

// === LIFECYCLE ===
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
/* === STAGGERING CSS CONDITIONNEL === */

/* États initiaux : tous les éléments sont cachés */
.stagger-1,
.stagger-2,
.stagger-3,
.stagger-4,
.stagger-5 {
  opacity: 0;
  transform: translateY(var(--space-05));
}

/* Animation d'entrée SEULEMENT si la classe animate-stagger est présente */
.librespot-player.animate-stagger .stagger-1,
.librespot-player.animate-stagger .stagger-2,
.librespot-player.animate-stagger .stagger-3,
.librespot-player.animate-stagger .stagger-4,
.librespot-player.animate-stagger .stagger-5 {
  animation: stagger-in 0.6s cubic-bezier(0.25, 1, 0.5, 1) forwards;
}

/* Si pas d'animation, afficher directement */
.librespot-player:not(.animate-stagger) .stagger-1,
.librespot-player:not(.animate-stagger) .stagger-2,
.librespot-player:not(.animate-stagger) .stagger-3,
.librespot-player:not(.animate-stagger) .stagger-4,
.librespot-player:not(.animate-stagger) .stagger-5 {
  opacity: 1;
  transform: translateY(0);
}

/* Délais échelonnés */
.librespot-player.animate-stagger .stagger-1 { animation-delay: 0ms; }
.librespot-player.animate-stagger .stagger-2 { animation-delay: 0ms; }
.librespot-player.animate-stagger .stagger-3 { animation-delay: 100ms; }
.librespot-player.animate-stagger .stagger-4 { animation-delay: 200ms; }
.librespot-player.animate-stagger .stagger-5 { animation-delay: 300ms; }

/* Animation d'entrée */
@keyframes stagger-in {
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* === STYLES DU COMPOSANT === */
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
  padding: var(--space-06) 0;
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