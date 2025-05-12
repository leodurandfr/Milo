<template>
  <div class="librespot-player">
    <div v-if="hasTrackInfo" class="now-playing">
      <NowPlayingInfo 
        :title="unifiedStore.metadata.title" 
        :artist="unifiedStore.metadata.artist" 
        :album="unifiedStore.metadata.album"
        :albumArtUrl="unifiedStore.metadata.album_art_url" 
      />
      <ProgressBar 
        :currentPosition="currentPosition" 
        :duration="duration"
        :progressPercentage="progressPercentage" 
        @seek="seekToPosition" 
      />
      <PlaybackControls 
        :isPlaying="unifiedStore.metadata.is_playing" 
        @play-pause="togglePlayPause" 
        @previous="previousTrack"
        @next="nextTrack" 
      />
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

import NowPlayingInfo from './NowPlayingInfo.vue';
import PlaybackControls from './PlaybackControls.vue';
import ProgressBar from './ProgressBar.vue';

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

// Observer les changements de métadonnées pour synchroniser la position
watch(() => unifiedStore.metadata, (newMetadata) => {
  if (newMetadata?.position !== undefined) {
    // La synchronisation est gérée dans usePlaybackProgress
  }
}, { immediate: true });

// AJOUT: Rafraîchir l'état complet au chargement de la page
onMounted(async () => {
  if (unifiedStore.currentSource === 'librespot') {
    try {
      const response = await axios.get('/librespot/status');
      if (response.data.status === 'ok') {
        // S'assurer que les métadonnées complètes sont utilisées
        const metadata = response.data.metadata || {};
        
        // Simuler un événement STATE_CHANGED complet
        unifiedStore.updateState({
          data: {
            full_state: {
              active_source: 'librespot',
              plugin_state: response.data.plugin_state,
              transitioning: false,
              metadata: metadata,  // Utiliser les métadonnées telles quelles
              error: null
            }
          }
        });
        
        // Log pour le débogage
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
  padding: 20px;
}

.now-playing {
  display: flex;
  flex-direction: column;
  gap: 20px;
  align-items: center;
  width: 100%;
}

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