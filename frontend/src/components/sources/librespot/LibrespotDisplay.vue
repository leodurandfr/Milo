<template>
  <div class="librespot-player">
    <div v-if="hasTrackInfo" class="now-playing">
      <NowPlayingInfo 
        :title="librespotStore.metadata.title" 
        :artist="librespotStore.metadata.artist" 
        :album="librespotStore.metadata.album"
        :albumArtUrl="librespotStore.metadata.album_art_url" 
      />
      <ProgressBar 
        :currentPosition="currentPosition" 
        :duration="duration"
        :progressPercentage="progressPercentage" 
        @seek="seekToPosition" 
      />
      <PlaybackControls 
        :isPlaying="librespotStore.isPlaying" 
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
    
    <div v-if="librespotStore.lastError" class="error-message">
      {{ librespotStore.lastError }}
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted } from 'vue';
import { useLibrespotStore } from '@/stores/librespot';
import { useLibrespotControl } from '@/composables/useLibrespotControl';
import { usePlaybackProgress } from '@/composables/usePlaybackProgress';
import useWebSocket from '@/services/websocket';

import NowPlayingInfo from './NowPlayingInfo.vue';
import PlaybackControls from './PlaybackControls.vue';
import ProgressBar from './ProgressBar.vue';

const librespotStore = useLibrespotStore();
const { togglePlayPause, previousTrack, nextTrack } = useLibrespotControl();
const { currentPosition, duration, progressPercentage, seekTo } = usePlaybackProgress();
const { on } = useWebSocket();

// Computed plus robuste pour vérifier si on a les infos de la piste
const hasTrackInfo = computed(() => {
  return !!(
    librespotStore.deviceConnected && 
    librespotStore.metadata?.title && 
    librespotStore.metadata?.artist
  );
});

function seekToPosition(position) {
  seekTo(position);
}

// Gestion des événements unifiés
function setupWebSocketEvents() {
  const unsubscriber = on('plugin_state_changed', data => {
    if (data.source === 'librespot') {
      librespotStore.handleWebSocketEvent('plugin_state_changed', data);
    }
  });

  return () => unsubscriber && unsubscriber();
}

const cleanup = setupWebSocketEvents();

onMounted(async () => {
  // Initialiser le store pour récupérer l'état actuel
  await librespotStore.initialize();
  
  // Si on a déjà des métadonnées, s'assurer que la progression est initialisée correctement
  if (hasTrackInfo.value) {
    if (librespotStore.metadata.position !== undefined) {
      seekTo(librespotStore.metadata.position);
    }
  }
});

onUnmounted(() => {
  cleanup();
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