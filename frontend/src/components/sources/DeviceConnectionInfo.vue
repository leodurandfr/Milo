<template>
  <div class="device-connection-info">
    <div class="connection-card">
      <!-- Icône adaptée à la source -->
      <div class="source-icon">
        <span v-if="source === 'bluetooth'">📱</span>
        <span v-else-if="source === 'macos'">💻</span>
        <span v-else>🔊</span>
      </div>
      
      <div class="device-info">
        <h3>{{ sourceTitle }}</h3>
        <p class="device-name">{{ deviceName }}</p>
        <p v-if="deviceAddress" class="device-address">{{ deviceAddress }}</p>
        
        <!-- Statut de connexion -->
        <div class="connection-status">
          <span class="status-indicator" :class="{ connected: isConnected }"></span>
          <span class="status-text">{{ isConnected ? 'Connecté' : 'En attente...' }}</span>
        </div>
        
        <!-- Actions principales -->
        <div class="actions">
          <button @click="onDisconnect" class="disconnect-button">
            Déconnecter
          </button>
          
          <!-- Actions spécifiques à la source -->
          <slot name="actions"></slot>
        </div>
      </div>
    </div>
    
    <!-- Section dispositifs découverts -->
    <div v-if="showDiscovered && discoveredDevices.length > 0" class="discovered-devices">
      <h4>{{ discoveredTitle }}</h4>
      <ul>
        <li v-for="device in discoveredDevices" :key="device.id || device.host || device.address" class="device-item">
          <div class="device-item-info">
            <span class="device-item-name">{{ getDeviceName(device) }}</span>
            <span v-if="getDeviceDetails(device)" class="device-item-details">
              {{ getDeviceDetails(device) }}
            </span>
          </div>
          <button @click="onConnectDevice(device)" class="connect-button">
            Connecter
          </button>
        </li>
      </ul>
    </div>
    
    <!-- Aucun appareil découvert -->
    <div v-else-if="showDiscovered" class="no-devices">
      <p>Aucun {{ sourceDeviceLabel }} découvert.</p>
      <button @click="onDiscoverDevices" class="discover-button">
        Rechercher {{ sourceDeviceLabel }}s
      </button>
    </div>
    
    <!-- Demande de connexion -->
    <div v-if="showConnectionRequest" class="connection-request-overlay">
      <div class="overlay" @click="onCancelRequest"></div>
      <div class="request-card">
        <div class="request-icon">
          <span v-if="source === 'bluetooth'">📱</span>
          <span v-else-if="source === 'macos'">💻</span>
          <span v-else>🔊</span>
        </div>
        
        <h3>Nouvelle connexion {{ sourceTitle }}</h3>
        
        <p>
          <strong>{{ connectionRequest.deviceName }}</strong> souhaite se connecter à oakOS.
          <span v-if="deviceName" class="current-device">
            Actuellement connecté à <strong>{{ deviceName }}</strong>
          </span>
        </p>
        
        <div class="request-actions">
          <button @click="onAcceptRequest" class="accept-button">
            Accepter
          </button>
          <button @click="onRejectRequest" class="reject-button">
            Refuser
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';

const props = defineProps({
  // Source audio (bluetooth, macos, etc.)
  source: {
    type: String,
    required: true
  },
  
  // Informations sur l'appareil connecté
  deviceInfo: {
    type: Object,
    default: () => ({})
  },
  
  // État de connexion
  isConnected: {
    type: Boolean,
    default: false
  },
  
  // Afficher la section des appareils découverts
  showDiscovered: {
    type: Boolean,
    default: false
  },
  
  // Liste des appareils découverts
  discoveredDevices: {
    type: Array,
    default: () => []
  },
  
  // Demande de connexion active
  connectionRequest: {
    type: Object,
    default: null
  },
  
  // Afficher la demande de connexion
  showConnectionRequest: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits([
  'disconnect',
  'discover-devices',
  'connect-device',
  'accept-request',
  'reject-request',
  'cancel-request'
]);

// Formatage des noms et labels en fonction de la source
const sourceTitle = computed(() => {
  switch (props.source) {
    case 'bluetooth': return 'Connexion Bluetooth';
    case 'macos': return 'Connexion MacOS';
    default: return 'Appareil connecté';
  }
});

const sourceDeviceLabel = computed(() => {
  switch (props.source) {
    case 'bluetooth': return 'appareil';
    case 'macos': return 'serveur';
    default: return 'périphérique';
  }
});

const discoveredTitle = computed(() => {
  switch (props.source) {
    case 'bluetooth': return 'Appareils disponibles';
    case 'macos': return 'Serveurs disponibles';
    default: return 'Périphériques disponibles';
  }
});

// Extraction des informations d'appareil
const deviceName = computed(() => {
  return props.deviceInfo.deviceName || 
         props.deviceInfo.name || 
         props.deviceInfo.host || 
         'Périphérique inconnu';
});

const deviceAddress = computed(() => {
  return props.deviceInfo.address || 
         props.deviceInfo.host || 
         props.deviceInfo.mac ||
         null;
});

// Fonctions utilitaires pour les appareils découverts
function getDeviceName(device) {
  return device.deviceName || 
         device.name || 
         device.host || 
         device.address || 
         'Périphérique inconnu';
}

function getDeviceDetails(device) {
  return device.address || 
         device.host || 
         device.mac ||
         device.lastSeen ? `Vu il y a ${formatTimeSince(device.lastSeen)}` : '';
}

function formatTimeSince(timestamp) {
  if (!timestamp) return '';
  
  const now = Date.now();
  const diff = Math.floor((now - timestamp) / 1000); // différence en secondes
  
  if (diff < 60) return 'à l\'instant';
  if (diff < 3600) return `${Math.floor(diff / 60)} min`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h`;
  return `${Math.floor(diff / 86400)} j`;
}

// Gestionnaires d'événements
function onDisconnect() {
  emit('disconnect');
}

function onDiscoverDevices() {
  emit('discover-devices');
}

function onConnectDevice(device) {
  emit('connect-device', device);
}

function onAcceptRequest() {
  emit('accept-request', props.connectionRequest);
}

function onRejectRequest() {
  emit('reject-request', props.connectionRequest);
}

function onCancelRequest() {
  emit('cancel-request');
}
</script>

<style scoped>
.device-connection-info {
  display: flex;
  flex-direction: column;
  width: 100%;
  max-width: 500px;
  position: relative;
}

.connection-card {
  background-color: #1E1E1E;
  border-radius: 10px;
  padding: 1.5rem;
  color: white;
  display: flex;
  align-items: center;
  margin-bottom: 1rem;
}

.source-icon {
  font-size: 3rem;
  margin-right: 1.5rem;
}

.device-info {
  flex-grow: 1;
}

.device-info h3 {
  margin-top: 0;
  margin-bottom: 0.5rem;
}

.device-name {
  font-size: 1.2rem;
  margin-top: 0.5rem;
  margin-bottom: 0.3rem;
}

.device-address {
  font-size: 0.9rem;
  color: #aaa;
  margin-bottom: 0.8rem;
}

.connection-status {
  display: flex;
  align-items: center;
  margin-bottom: 1rem;
}

.status-indicator {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background-color: #F44336;
  margin-right: 0.5rem;
}

.status-indicator.connected {
  background-color: #4CAF50;
}

.status-text {
  font-size: 0.9rem;
}

.actions {
  display: flex;
  gap: 0.8rem;
}

.disconnect-button {
  background-color: #F44336;
  color: white;
  border: none;
  padding: 0.5rem 1rem;
  border-radius: 4px;
  cursor: pointer;
  transition: background-color 0.2s;
}

.disconnect-button:hover {
  background-color: #E53935;
}

.discovered-devices {
  background-color: #1E1E1E;
  border-radius: 10px;
  padding: 1.5rem;
  color: white;
  margin-bottom: 1rem;
}

.discovered-devices h4 {
  margin-top: 0;
  margin-bottom: 1rem;
  font-size: 1.1rem;
}

.discovered-devices ul {
  list-style: none;
  padding: 0;
  margin: 0;
}

.device-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.8rem 0;
  border-bottom: 1px solid #333;
}

.device-item:last-child {
  border-bottom: none;
}

.device-item-info {
  display: flex;
  flex-direction: column;
}

.device-item-name {
  font-size: 1rem;
  margin-bottom: 0.2rem;
}

.device-item-details {
  font-size: 0.8rem;
  color: #aaa;
}

.connect-button {
  background-color: #4CAF50;
  color: white;
  border: none;
  padding: 0.4rem 0.8rem;
  border-radius: 4px;
  cursor: pointer;
  transition: background-color 0.2s;
  font-size: 0.9rem;
}

.connect-button:hover {
  background-color: #43A047;
}

.no-devices {
  background-color: #1E1E1E;
  border-radius: 10px;
  padding: 1.5rem;
  color: white;
  text-align: center;
}

.no-devices p {
  margin: 0.5rem 0;
}

.discover-button {
  background-color: #007BFF;
  color: white;
  border: none;
  padding: 0.5rem 1rem;
  border-radius: 4px;
  cursor: pointer;
  transition: background-color 0.2s;
  margin-top: 0.8rem;
}

.discover-button:hover {
  background-color: #0069D9;
}

/* Styles pour la demande de connexion */
.connection-request-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.overlay {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background-color: rgba(0, 0, 0, 0.7);
  z-index: -1;
}

.request-card {
  background-color: #1E1E1E;
  border-radius: 12px;
  padding: 2rem;
  color: white;
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 90%;
  max-width: 400px;
  box-shadow: 0 10px 25px rgba(0, 0, 0, 0.5);
  animation: slideIn 0.3s ease-out;
}

@keyframes slideIn {
  from {
    transform: translateY(-30px);
    opacity: 0;
  }
  to {
    transform: translateY(0);
    opacity: 1;
  }
}

.request-icon {
  font-size: 3rem;
  margin-bottom: 1rem;
}

.request-card h3 {
  font-size: 1.5rem;
  margin-bottom: 1rem;
  text-align: center;
}

.request-card p {
  text-align: center;
  margin-bottom: 1.5rem;
  line-height: 1.4;
}

.current-device {
  display: block;
  margin-top: 0.5rem;
  font-size: 0.9rem;
  color: #aaa;
}

.request-actions {
  display: flex;
  gap: 1rem;
  width: 100%;
  justify-content: center;
}

.accept-button, .reject-button {
  padding: 0.75rem 1.5rem;
  border: none;
  border-radius: 6px;
  font-weight: bold;
  cursor: pointer;
  transition: background-color 0.2s;
  flex: 1;
  max-width: 120px;
}

.accept-button {
  background-color: #4CAF50;
  color: white;
}

.accept-button:hover {
  background-color: #43A047;
}

.reject-button {
  background-color: #F44336;
  color: white;
}

.reject-button:hover {
  background-color: #E53935;
}
</style>