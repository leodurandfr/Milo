<template>
    <div class="librespot-player">
      <!-- État connecté avec métadonnées -->
      <div class="now-playing" v-if="!audioStore.isDisconnected && hasTrackInfo">
        <NowPlayingInfo :title="metadata.title" :artist="metadata.artist" :album="metadata.album"
          :albumArtUrl="metadata.album_art_url" />
        <ProgressBar :currentPosition="currentPosition" :duration="metadata.duration_ms"
          :progressPercentage="progressPercentage" @seek="seekToPosition" />
        <PlaybackControls :isPlaying="metadata.is_playing" @play-pause="togglePlayPause" @previous="previousTrack"
          @next="nextTrack" />
      </div>
  
      <!-- État en attente de connexion -->
      <div v-else class="waiting-connection">
        <div class="waiting-icon">🎵</div>
        <h3>En attente de connexion</h3>
        <p>Connectez un appareil via Spotify Connect</p>
      </div>
    </div>
  </template>
  
  <script setup>
  import { computed, onMounted } from 'vue';
  import { useAudioStore } from '@/stores/index';
  import { useLibrespotControl } from '@/composables/useLibrespotControl';
  import { usePlaybackProgress } from '@/composables/usePlaybackProgress';
  import NowPlayingInfo from './NowPlayingInfo.vue';
  import PlaybackControls from './PlaybackControls.vue';
  import ProgressBar from './ProgressBar.vue';
  
  const audioStore = useAudioStore();
  const { togglePlayPause, previousTrack, nextTrack, seekTo: controlSeekTo } = useLibrespotControl();
  const { currentPosition, progressPercentage, seekTo: trackingSeekTo } = usePlaybackProgress();
  
  const metadata = computed(() => audioStore.metadata || {});
  
  // Vérifier si on a des infos sur la piste
  const hasTrackInfo = computed(() => 
    metadata.value && metadata.value.title && metadata.value.artist
  );
  
  function seekToPosition(position) {
    trackingSeekTo(position);
    controlSeekTo(position);
  }
  
  onMounted(() => {
    // Vérifier l'état initial
    audioStore.controlSource('librespot', 'get_status');
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
  </style>