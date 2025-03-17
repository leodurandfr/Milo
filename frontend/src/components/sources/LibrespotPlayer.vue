<script setup>
import { computed, ref, onMounted, onUnmounted, watch } from 'vue';
import { useAudioStore } from '@/stores/audio';
import { useLibrespotControl } from '@/composables/useLibrespotControl';
import { usePlaybackProgress } from '@/composables/usePlaybackProgress';
import axios from 'axios';

const audioStore = useAudioStore();
const { togglePlayPause, previousTrack, nextTrack } = useLibrespotControl();
// Utiliser notre service de progression pour suivre la position en temps réel
const { currentPosition, progressPercentage, seekTo } = usePlaybackProgress();

const showDebugInfo = ref(false);
const statusResult = ref(null);
const lastMessages = ref([]);
const debugCheckInterval = ref(null);
const connectionCheckInterval = ref(null);
const manualConnectionStatus = ref(null);
const connectionLastChecked = ref(Date.now());
const connectionTimeoutMs = 20000;

// État pour stocker si nous sommes réellement connectés
const isActuallyConnected = ref(false);

const metadata = computed(() => {
  console.log("Metadata dans le composant LibrespotPlayer:", audioStore.metadata);
  return audioStore.metadata || {};
});

// Surveillez l'état de déconnexion dans le store
watch(() => audioStore.isDisconnected, (newValue) => {
  if (newValue === true) {
    console.log("État de déconnexion détecté dans le store, mise à jour de isActuallyConnected");
    isActuallyConnected.value = false;
  }
});

// Fonction pour maintenir l'état de connexion
watch(() => [
    metadata.value?.deviceConnected, 
    metadata.value?.connected, 
    metadata.value?.is_playing
  ], 
  (newValues, oldValues) => {
    if (!newValues || !oldValues) return;
    
    // S'assurer que newValues et oldValues sont des tableaux
    const [newDeviceConnected, newConnected, newIsPlaying] = newValues || [false, false, false];
    const [oldDeviceConnected, oldConnected, oldIsPlaying] = oldValues || [false, false, false];
    
    console.log("Changement d'état de connexion détecté:", 
      {deviceConnected: {old: oldDeviceConnected, new: newDeviceConnected}, 
       connected: {old: oldConnected, new: newConnected},
       is_playing: {old: oldIsPlaying, new: newIsPlaying}});
    
    // Si le store indique une déconnexion, prioritaire sur tout
    if (audioStore.isDisconnected) {
      console.log("Store indique déconnexion explicite, force déconnexion");
      isActuallyConnected.value = false;
      return;
    }
       
    // Si une des nouvelles valeurs indique une connexion, on est connecté
    if (newDeviceConnected || newConnected || newIsPlaying) {
      // Vérifier aussi qu'il y a des métadonnées valides
      if (Object.keys(metadata.value).length > 0 && !audioStore.isDisconnected) {
        console.log("Connexion détectée via metadata");
        isActuallyConnected.value = true;
        connectionLastChecked.value = Date.now();
      } else {
        console.log("Connexion suggérée mais pas de métadonnées valides");
        // Vérifier avec l'API
        checkStatus();
      }
    }
    
    // Si toutes les valeurs indiquent une déconnexion et que nous pensions être connectés
    if (isActuallyConnected.value && 
        !newDeviceConnected && !newConnected && !newIsPlaying) {
      console.log("Possible déconnexion détectée via metadata, vérification...");
      
      // Vérifier explicitement l'état de connexion avec l'API
      checkStatus();
    }
    
    // Si en mode débogage, permettre une substitution manuelle
    if (manualConnectionStatus.value !== null) {
      isActuallyConnected.value = manualConnectionStatus.value;
    }
  }
);

// Vérifier si un appareil est connecté (connexion Spotify établie)
const deviceConnected = computed(() => {
  return !audioStore.isDisconnected;
});

// Vérifier si on a des informations sur la piste en cours
const hasTrackInfo = computed(() => {
  return metadata.value && 
         metadata.value.title && 
         metadata.value.artist && 
         !audioStore.isDisconnected;
});

function formatTime(ms) {
  if (!ms) return '0:00';
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, '0')}`;
}

function toggleDebugInfo() {
  showDebugInfo.value = !showDebugInfo.value;
}

function toggleConnectionStatus() {
  if (manualConnectionStatus.value === null) {
    manualConnectionStatus.value = !deviceConnected.value;
  } else {
    manualConnectionStatus.value = !manualConnectionStatus.value;
  }
  console.log("État de connexion forcé à:", manualConnectionStatus.value);
}

function resetConnectionStatus() {
  manualConnectionStatus.value = null;
  console.log("État de connexion réinitialisé (autodétection)");
}

async function checkStatus() {
  try {
    // Utiliser la fonction du store pour vérifier l'état de connexion
    const isConnected = await audioStore.checkConnectionStatus();
    
    // Mettre à jour l'état local basé sur la réponse
    isActuallyConnected.value = isConnected;
    
    // Mettre à jour l'horodatage si connecté
    if (isConnected) {
      connectionLastChecked.value = Date.now();
    }
    
    // Ajouter ce résultat aux derniers messages pour le débogage
    addDebugMessage("api_connection_check", { connected: isConnected });
    
    // En mode débogage, mettre à jour les informations détaillées
    if (showDebugInfo.value) {
      const response = await axios.get('/api/librespot/status');
      statusResult.value = response.data;
      addDebugMessage("status_check", statusResult.value);
    }
    
    return isConnected;
  } catch (error) {
    console.error("Error checking status:", error);
    
    // En mode débogage, afficher l'erreur
    if (showDebugInfo.value) {
      statusResult.value = { error: error.message };
      addDebugMessage("status_check_error", { error: error.message });
    }
    
    // En cas d'erreur, considérer comme déconnecté
    isActuallyConnected.value = false;
    return false;
  }
}

function addDebugMessage(type, data) {
  const now = new Date();
  const timeString = now.toLocaleTimeString();
  
  lastMessages.value.unshift({
    timestamp: timeString,
    type: type,
    data: data
  });
  
  if (lastMessages.value.length > 5) {
    lastMessages.value.pop();
  }
}

async function forceRefreshMetadata() {
  try {
    // D'abord, vérifier l'état de connexion
    const isConnected = await checkStatus();
    
    if (isConnected) {
      // Si connecté, demander une actualisation des métadonnées
      await audioStore.controlSource('librespot', 'refresh_metadata');
      addDebugMessage("refresh_metadata", { timestamp: new Date().toISOString() });
    } else {
      // Si déconnecté, effacer les métadonnées
      audioStore.clearMetadata();
      addDebugMessage("metadata_cleared", { reason: "déconnecté" });
    }
  } catch (error) {
    console.error("Error refreshing metadata:", error);
    addDebugMessage("refresh_error", { error: error.message });
  }
}

function checkConnectionTimeout() {
  const timeSinceLastCheck = Date.now() - connectionLastChecked.value;
  
  // Si nous sommes connectés mais que nous n'avons pas reçu de mise à jour depuis longtemps
  if (isActuallyConnected.value && timeSinceLastCheck > connectionTimeoutMs) {
    console.log(`Délai de connexion dépassé (${timeSinceLastCheck}ms), vérification...`);
    checkStatus();
  }
  
  // Vérifier périodiquement même si nous pensons être déconnectés
  if (!isActuallyConnected.value && timeSinceLastCheck > connectionTimeoutMs * 2) {
    console.log("Vérification périodique de l'état de connexion...");
    checkStatus();
    connectionLastChecked.value = Date.now();  // Mettre à jour même si déconnecté
  }
}

// Nouvelle fonction pour gérer les clics sur la barre de progression
function onProgressClick(event) {
  if (!metadata.value?.duration_ms) return;
  
  const container = event.currentTarget;
  const rect = container.getBoundingClientRect();
  const offsetX = event.clientX - rect.left;
  const percentage = offsetX / rect.width;
  
  // Calculer la nouvelle position en ms
  const newPosition = Math.floor(metadata.value.duration_ms * percentage);
  
  // Appliquer la nouvelle position via le service de progression
  seekTo(newPosition);
}

onMounted(() => {
  // Vérification initiale
  setTimeout(() => {
    checkStatus();
  }, 2000);
  
  // Vérifications périodiques en mode débogage
  debugCheckInterval.value = setInterval(() => {
    if (showDebugInfo.value) {
      checkStatus();
    }
  }, 10000);
  
  // Vérification périodique de l'état de connexion
  connectionCheckInterval.value = setInterval(() => {
    checkConnectionTimeout();
  }, 10000);
});

onUnmounted(() => {
  if (debugCheckInterval.value) {
    clearInterval(debugCheckInterval.value);
  }
  
  if (connectionCheckInterval.value) {
    clearInterval(connectionCheckInterval.value);
  }
  
  manualConnectionStatus.value = null;
});
</script>

<template>
  <div class="librespot-player">
    <!-- Affichage quand librespot est actif et qu'un périphérique est connecté avec métadonnées -->
    <div class="now-playing" v-if="deviceConnected && hasTrackInfo">
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
        <!-- Utiliser currentPosition du service de progression au lieu de metadata.position_ms -->
        <span class="time current">{{ formatTime(currentPosition) }}</span>
        <!-- Ajouter le gestionnaire de clic pour permettre le seek -->
        <div class="progress-container" @click="onProgressClick">
          <!-- Utiliser progressPercentage du service de progression -->
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

    <!-- Affichage quand aucun périphérique n'est connecté OU pas de métadonnées -->
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
    
    <!-- Panneau de débogage (affiché uniquement quand showDebugInfo est true) -->
    <div v-if="showDebugInfo" class="debug-info">
      <h4>Debug Information</h4>
      <div class="debug-buttons">
        <button @click="checkStatus" class="debug-button check-status">Vérifier statut</button>
        <button @click="forceRefreshMetadata" class="debug-button refresh">Rafraîchir métadonnées</button>
        <button @click="toggleConnectionStatus" class="debug-button connection">
          {{ manualConnectionStatus === null ? 'Forcer connexion' : (manualConnectionStatus ? 'Forcer déconnexion' : 'Forcer connexion') }}
        </button>
        <button @click="resetConnectionStatus" class="debug-button reset">Réinitialiser auto-détection</button>
      </div>
      
      <h4>Current Playback</h4>
      <p>Estimated position: {{ currentPosition }} ms ({{ formatTime(currentPosition) }})</p>
      <p>Progress: {{ progressPercentage.toFixed(2) }}%</p>
      <p>Store isPlaying: {{ audioStore.isPlaying }}</p>
      
      <h4>Status</h4>
      <pre>{{ JSON.stringify(statusResult, null, 2) }}</pre>
      
      <h4>Metadata</h4>
      <pre>{{ JSON.stringify(metadata, null, 2) }}</pre>
      
      <h4>Connection</h4>
      <p>isActuallyConnected: {{ isActuallyConnected }}</p>
      <p>deviceConnected: {{ deviceConnected }}</p>
      <p>hasTrackInfo: {{ hasTrackInfo }}</p>
      <p>isDisconnected: {{ audioStore.isDisconnected }}</p>
      <p>manualConnectionStatus: {{ manualConnectionStatus }}</p>
      <p>Last checked: {{ new Date(connectionLastChecked).toLocaleTimeString() }}</p>
      
      <h4>Derniers messages</h4>
      <div class="ws-debug">
        <div v-for="(msg, index) in lastMessages" :key="index" class="ws-message">
          <div class="ws-timestamp">{{ msg.timestamp }} - {{ msg.type }}</div>
          <pre>{{ JSON.stringify(msg.data, null, 2) }}</pre>
        </div>
      </div>
    </div>
    
    <!-- Bouton pour afficher/masquer le débogage -->
    <button @click="toggleDebugInfo" class="debug-toggle-button">
      {{ showDebugInfo ? 'Masquer debug' : 'Afficher debug' }}
    </button>
  </div>
</template>

<style scoped>
/* Styles de base */
.librespot-player {
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.now-playing, .device-connected, .waiting-connection {
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

/* Styles pour l'album et les contrôles */
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
  height: 8px; /* Augmenté pour faciliter le clic */
  background-color: #4D4D4D;
  border-radius: 4px;
  margin: 0 10px;
  cursor: pointer; /* Indique que c'est cliquable */
  position: relative;
}

.progress {
  height: 100%;
  background-color: #1DB954;
  border-radius: 4px;
  transition: width 0.1s linear; /* Animation fluide */
}

.time {
  font-size: 0.8rem;
  opacity: 0.8;
  font-variant-numeric: tabular-nums;
  min-width: 40px; /* Assure une largeur minimale pour éviter le saut */
  text-align: center;
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

/* Styles pour les écrans d'attente */
.device-connected {
  text-align: center;
}

.instruction {
  margin-top: 1rem;
  font-weight: bold;
  color: #1DB954;
}

.waiting-connection, .device-connected {
  padding: 2rem;
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

.waiting-connection h3, .device-connected h3 {
  font-size: 1.5rem;
  margin-bottom: 0.5rem;
}

.waiting-connection p, .device-connected p {
  opacity: 0.8;
  max-width: 400px;
  margin: 0 auto 0.5rem;
}

/* Styles pour le débogage */
.debug-toggle-button {
  margin-top: 1rem;
  background-color: #333;
  color: white;
  border: none;
  padding: 0.4rem 0.8rem;
  border-radius: 4px;
  font-size: 0.8rem;
  cursor: pointer;
}

.debug-info {
  margin-top: 1.5rem;
  background-color: #2a2a2a;
  padding: 1rem;
  border-radius: 5px;
  text-align: left;
  max-width: 100%;
  overflow-x: auto;
  width: 100%;
}

.debug-info h4 {
  margin-top: 0.5rem;
  margin-bottom: 0.5rem;
  color: #1DB954;
}

.debug-info p {
  margin: 0.2rem 0;
  font-family: monospace;
}

.debug-info pre {
  white-space: pre-wrap;
  font-size: 0.8rem;
  color: #00ff00;
  background-color: #1a1a1a;
  padding: 0.5rem;
  border-radius: 3px;
  overflow-x: auto;
  margin: 0.5rem 0;
}

.debug-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-top: 1rem;
  justify-content: center;
}

.debug-button {
  background-color: #444;
  color: white;
  border: none;
  padding: 0.5rem 1rem;
  border-radius: 4px;
  cursor: pointer;
  transition: background-color 0.2s;
}

.debug-button:hover {
  background-color: #555;
}

.check-status {
  background-color: #1DB954;
}

.check-status:hover {
  background-color: #19a449;
}

.refresh {
  background-color: #0077cc;
}

.refresh:hover {
  background-color: #0066b3;
}

.connection {
  background-color: #ff9800;
}

.connection:hover {
  background-color: #e68a00;
}

.reset {
  background-color: #f44336;
}

.reset:hover {
  background-color: #d32f2f;
}

/* Styles pour l'historique WebSocket */
.ws-debug {
  max-height: 300px;
  overflow-y: auto;
  background-color: #1a1a1a;
  border-radius: 3px;
  padding: 0.5rem;
  margin: 0.5rem 0;
}

.ws-message {
  margin-bottom: 0.5rem;
  border-bottom: 1px solid #333;
  padding-bottom: 0.5rem;
}

.ws-message:last-child {
  margin-bottom: 0;
  border-bottom: none;
}

.ws-timestamp {
  font-size: 0.7rem;
  color: #999;
  margin-bottom: 0.2rem;
}
</style>