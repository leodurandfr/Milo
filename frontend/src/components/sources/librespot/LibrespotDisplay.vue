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
    
    <div v-if="audioStore.systemState.error && audioStore.currentSource === 'librespot'" class="error-message">
      {{ audioStore.systemState.error }}
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue';
import { useLibrespotStore } from '@/stores/librespot';
import { useAudioStore } from '@/stores/audioStore';
import { useLibrespotControl } from '@/composables/useLibrespotControl';
import { usePlaybackProgress } from '@/composables/usePlaybackProgress';

import NowPlayingInfo from './NowPlayingInfo.vue';
import PlaybackControls from './PlaybackControls.vue';
import ProgressBar from './ProgressBar.vue';

const librespotStore = useLibrespotStore();
const audioStore = useAudioStore();
const { togglePlayPause, previousTrack, nextTrack } = useLibrespotControl();
const { currentPosition, duration, progressPercentage, seekTo, initializePosition } = usePlaybackProgress();

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

onMounted(() => {  
  // Initialiser la position sans faire de seek
  if (hasTrackInfo.value && librespotStore.metadata.position !== undefined) {
    initializePosition(librespotStore.metadata.position);
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