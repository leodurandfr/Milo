<template>
  <div class="librespot-player">
    <!-- Affichage quand librespot est actif et qu'un périphérique est connecté -->
    <div class="now-playing" v-if="hasMetadata">
      <div class="album-art">
        <img v-if="metadata.album_art_url" :src="metadata.album_art_url" alt="Album Art" />
        <div v-else class="placeholder-art">
          <span class="music-icon">🎵</span>
        </div>
      </div>

      <div class="track-info">
        <h3 class="track-title">{{ metadata.title || 'Titre inconnu' }}</h3>
        <p class="track-artist">{{ metadata.artist || 'Artiste inconnu' }}</p>
        <p class="track-album" v-if="metadata.album">{{ metadata.album }}</p>
      </div>

      <div class="progress-bar" v-if="metadata.duration_ms">
        <span class="time current">{{ formatTime(metadata.position_ms) }}</span>
        <div class="progress-container">
          <div class="progress" :style="{ width: progressPercentage + '%' }"></div>
        </div>
        <span class="time total">{{ formatTime(metadata.duration_ms) }}</span>
      </div>

      <div class="controls">
        <button @click="previousTrack" class="control-button previous">
          <span>⏮️</span>
        </button>
        <button @click="togglePlayPause" class="control-button play-pause">
          <span>{{ metadata.is_playing ? '⏸️' : '▶️' }}</span>
        </button>
        <button @click="nextTrack" class="control-button next">
          <span>⏭️</span>
        </button>
      </div>
    </div>

    <!-- Affichage quand librespot est actif mais qu'aucun périphérique n'est connecté -->
    <div class="waiting-connection" v-else>
      <div class="waiting-content">
        <div class="spotify-logo">
          <img src="https://storage.googleapis.com/pr-newsroom-wp/1/2018/11/Spotify_Logo_RGB_Green.png"
            alt="Spotify Logo">
        </div>
        <h3>En attente de connexion</h3>
        <p>Ouvrez l'application Spotify sur votre appareil et sélectionnez "oakOS" comme périphérique de lecture</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useAudioStore } from '@/stores/audio';
import { useLibrespotControl } from '@/composables/useLibrespotControl';

const audioStore = useAudioStore();
const { togglePlayPause, previousTrack, nextTrack } = useLibrespotControl();

const metadata = computed(() => {
  return audioStore.metadata || {};
});

const hasMetadata = computed(() => {
  // Débogage: afficher les métadonnées reçues
  console.log("Metadata reçues:", metadata.value);

  // Une piste est considérée comme chargée si l'un des champs suivants existe
  return metadata.value && (
    metadata.value.title ||
    metadata.value.artist ||
    metadata.value.is_playing === true
  );
});

const progressPercentage = computed(() => {
  if (!metadata.value.duration_ms || !metadata.value.position_ms) return 0;
  return (metadata.value.position_ms / metadata.value.duration_ms) * 100;
});

function formatTime(ms) {
  if (!ms) return '0:00';
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, '0')}`;
}
</script>

<style scoped>
.now-playing-container {
  margin-top: 1.5rem;
  width: 100%;
}

.now-playing {
  background-color: #1E1E1E;
  border-radius: 10px;
  padding: 1.5rem;
  color: white;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.album-art {
  width: 200px;
  height: 200px;
  margin-bottom: 1.5rem;
  box-shadow: 0 8px 16px rgba(0, 0, 0, 0.3);
  border-radius: 5px;
  overflow: hidden;
}

.album-art img {
  width: 100%;
  height: 100%;
  object-fit: cover;
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

.track-info {
  text-align: center;
  margin-bottom: 1.5rem;
  width: 100%;
}

.track-title {
  font-size: 1.5rem;
  font-weight: bold;
  margin-bottom: 0.5rem;
}

.track-artist {
  font-size: 1.2rem;
  opacity: 0.8;
  margin-bottom: 0.3rem;
}

.track-album {
  font-size: 1rem;
  opacity: 0.6;
}

.progress-bar {
  display: flex;
  align-items: center;
  width: 100%;
  margin-bottom: 1.5rem;
}

.progress-container {
  flex-grow: 1;
  height: 4px;
  background-color: #4D4D4D;
  border-radius: 2px;
  margin: 0 10px;
}

.progress {
  height: 100%;
  background-color: #1DB954;
  border-radius: 2px;
}

.time {
  font-size: 0.8rem;
  opacity: 0.8;
  font-variant-numeric: tabular-nums;
}

.controls {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 1.5rem;
}

.control-button {
  background: none;
  border: none;
  cursor: pointer;
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 50px;
  height: 50px;
  border-radius: 50%;
  transition: background-color 0.2s;
  font-size: 1.5rem;
}

.control-button:hover {
  background-color: rgba(255, 255, 255, 0.1);
}

.control-button.play-pause {
  width: 60px;
  height: 60px;
  font-size: 2rem;
}

.waiting-connection {
  background-color: #1E1E1E;
  border-radius: 10px;
  padding: 2rem;
  text-align: center;
  color: white;
}

.waiting-content {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.spotify-logo {
  width: 150px;
  margin-bottom: 1.5rem;
}

.spotify-logo img {
  width: 100%;
  height: auto;
}

.waiting-connection h3 {
  font-size: 1.5rem;
  margin-bottom: 0.5rem;
}

.waiting-connection p {
  opacity: 0.8;
  max-width: 400px;
  margin: 0 auto;
}
</style>