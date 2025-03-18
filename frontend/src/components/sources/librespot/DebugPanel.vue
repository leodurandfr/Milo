<template>
    <div class="debug-info">
      <h4>Debug Information</h4>
      <div class="debug-buttons">
        <button @click="onCheckStatus" class="debug-button check-status">Vérifier statut</button>
        <button @click="onRefreshMetadata" class="debug-button refresh">Rafraîchir métadonnées</button>
        <button @click="onToggleConnection" class="debug-button connection">
          {{ manualConnectionStatus === null ? 'Forcer connexion' : (manualConnectionStatus ? 'Forcer déconnexion' :
          'Forcer connexion') }}
        </button>
        <button @click="onResetConnection" class="debug-button reset">Réinitialiser auto-détection</button>
      </div>
  
      <h4>Current Playback</h4>
      <p>Estimated position: {{ currentPosition }} ms ({{ formatTime(currentPosition) }})</p>
      <p>Progress: {{ progressPercentage.toFixed(2) }}%</p>
      <p>Store isPlaying: {{ isPlaying }}</p>
  
      <h4>Status</h4>
      <pre>{{ JSON.stringify(statusResult, null, 2) }}</pre>
  
      <h4>Metadata</h4>
      <pre>{{ JSON.stringify(metadata, null, 2) }}</pre>
  
      <h4>Connection</h4>
      <p>isActuallyConnected: {{ isActuallyConnected }}</p>
      <p>deviceConnected: {{ deviceConnected }}</p>
      <p>hasTrackInfo: {{ hasTrackInfo }}</p>
      <p>isDisconnected: {{ isDisconnected }}</p>
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
  </template>
  
  <script setup>
  import { computed } from 'vue';
  
  const props = defineProps({
    statusResult: Object,
    metadata: Object,
    currentPosition: Number,
    progressPercentage: Number,
    isPlaying: Boolean,
    isActuallyConnected: Boolean,
    deviceConnected: Boolean,
    hasTrackInfo: Boolean,
    isDisconnected: Boolean,
    manualConnectionStatus: {
      type: Boolean,
      default: null
    },
    connectionLastChecked: Number,
    lastMessages: Array
  });
  
  const emit = defineEmits([
    'check-status', 
    'refresh-metadata', 
    'toggle-connection', 
    'reset-connection'
  ]);
  
  function formatTime(ms) {
    if (!ms) return '0:00';
    const totalSeconds = Math.floor(ms / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${minutes}:${seconds.toString().padStart(2, '0')}`;
  }
  
  function onCheckStatus() {
    emit('check-status');
  }
  
  function onRefreshMetadata() {
    emit('refresh-metadata');
  }
  
  function onToggleConnection() {
    emit('toggle-connection');
  }
  
  function onResetConnection() {
    emit('reset-connection');
  }
  </script>
  
  <style scoped>
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