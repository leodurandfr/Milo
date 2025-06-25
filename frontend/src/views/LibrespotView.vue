<template>
  <div class="librespot-player">
    <div v-if="hasTrackInfo" class="now-playing">
      <!-- Partie gauche : Image de couverture -->
      <div class="album-art-section">
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
        <div class="track-info">
          <h1 class="track-title heading-1">{{ unifiedStore.metadata.title || 'Titre inconnu' }}</h1>
          <p class="track-artist heading-2">{{ unifiedStore.metadata.artist || 'Artiste inconnu' }}</p>
        </div>

        <!-- Bloc 2 : Contrôles (aligné en bas) -->
        <div class="controls-section">
          <ProgressBar :currentPosition="currentPosition" :duration="duration" :progressPercentage="progressPercentage"
            @seek="seekToPosition" />
          <PlaybackControls :isPlaying="unifiedStore.metadata.is_playing" @play-pause="togglePlayPause"
            @previous="previousTrack" @next="nextTrack" />
        </div>
      </div>
    </div>

    <div v-else class="waiting-connection">
      <div class="waiting-icon">🎵</div>
      <h3>En attente de connexion</h3>
      <p>Connectez un appareil via Spotify Connect</p>
    </div>

    <div v-if="unifiedStore.error && unifiedStore.currentSource === 'librespot'" class="error-message">
      {{ unifiedStore.error }}
    </div>
  </div>
</template>

<script setup>
import { computed, watch, onMounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useLibrespotControl } from '@/composables/useLibrespotControl';
import { usePlaybackProgress } from '@/composables/usePlaybackProgress';
import axios from 'axios';

import PlaybackControls from '../components/librespot/PlaybackControls.vue';
import ProgressBar from '../components/librespot/ProgressBar.vue';

const unifiedStore = useUnifiedAudioStore();
const { togglePlayPause, previousTrack, nextTrack } = useLibrespotControl();
const { currentPosition, duration, progressPercentage, seekTo } = usePlaybackProgress();

const hasTrackInfo = computed(() => {
  return !!(
    unifiedStore.currentSource === 'librespot' &&
    unifiedStore.pluginState === 'connected' &&
    unifiedStore.metadata?.title &&
    unifiedStore.metadata?.artist
  );
});

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
  padding: var(--space-06);
  gap: var(--space-06);
}

/* Partie gauche : Image de couverture */
.album-art-section {
  flex-shrink: 0;
  height: 100%;
  aspect-ratio: 1;
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

/* Partie droite : Contenu */
.content-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

/* Bloc 1 : Informations (prend l'espace restant) */
.track-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  text-align: center;
  gap: var(--space-04);
}

.track-title {
  color: var(--color-text);
}
.track-artist {
  color: var(--color-text-secondary);
}

/* Bloc 2 : Contrôles (aligné en bas) */
.controls-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

/* État d'attente (layout actuel conservé) */
.waiting-connection {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 300px;
  color: #666;
}

.waiting-icon {
  font-size: 48px;
  margin-bottom: 20px;
}

.error-message {
  color: #ff4444;
  margin-top: 10px;
  text-align: center;
  padding: 10px;
  background-color: #fff0f0;
  border-radius: 4px;
}
</style>