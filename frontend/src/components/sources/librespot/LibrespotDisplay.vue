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
import { computed, onMounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useLibrespotControl } from '@/composables/useLibrespotControl';
import { usePlaybackProgress } from '@/composables/usePlaybackProgress';

import NowPlayingInfo from './NowPlayingInfo.vue';
import PlaybackControls from './PlaybackControls.vue';
import ProgressBar from './ProgressBar.vue';

const unifiedStore = useUnifiedAudioStore();
const { togglePlayPause, previousTrack, nextTrack } = useLibrespotControl();
const { currentPosition, duration, progressPercentage, seekTo, initializePosition } = usePlaybackProgress();

const hasTrackInfo = computed(() => {
  return !!(
    unifiedStore.currentSource === 'librespot' &&
    unifiedStore.metadata?.title && 
    unifiedStore.metadata?.artist
  );
});

function seekToPosition(position) {
  seekTo(position);
}

onMounted(() => {  
  if (hasTrackInfo.value && unifiedStore.metadata.position !== undefined) {
    initializePosition(unifiedStore.metadata.position);
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