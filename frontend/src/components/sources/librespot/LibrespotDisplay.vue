<template>
  <div class="librespot-player">
    <div v-if="librespotStore.isConnected && librespotStore.hasTrackInfo" class="now-playing">
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
      <p v-if="librespotStore.connectionStatus === 'error'" class="error-message">
        {{ librespotStore.lastError }}
      </p>
      <p v-else>Connectez un appareil via Spotify Connect</p>
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

function seekToPosition(position) {
  seekTo(position);
}

// Gestion des événements spécifiques à librespot
function setupWebSocketEvents() {
  const events = {
    'librespot_metadata_updated': data => {
      if (data.source === 'librespot') {
        librespotStore.handleWebSocketEvent('librespot_metadata_updated', data);
      }
    },
    'librespot_status_updated': data => {
      if (data.source === 'librespot') {
        librespotStore.handleWebSocketEvent('librespot_status_updated', data);
      }
    },
    'librespot_seek': data => {
      if (data.source === 'librespot') {
        librespotStore.handleWebSocketEvent('librespot_seek', data);
      }
    }
  };

  const unsubscribers = Object.entries(events).map(
    ([event, handler]) => on(event, handler)
  );

  return () => unsubscribers.forEach(unsub => unsub && unsub());
}

// Configuration avant le montage
const cleanup = setupWebSocketEvents();

onMounted(async () => {
  await librespotStore.initialize();
});

onUnmounted(() => {
  cleanup();
});
</script>

<style scoped>
.librespot-player {
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.now-playing {
  background-color: #1E1E1E;
  border-radius: 10px;
  padding: 1.5rem;
  color: white;
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 100%;
  max-width: 500px;
}

.waiting-connection {
  background-color: #1E1E1E;
  border-radius: 10px;
  padding: 2rem;
  color: white;
  text-align: center;
  width: 100%;
  max-width: 500px;
}

.waiting-icon {
  font-size: 3rem;
  margin-bottom: 1rem;
  opacity: 0.6;
}

.error-message {
  color: #ff5252;
  margin-top: 1rem;
}
</style>