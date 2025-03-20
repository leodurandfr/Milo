<template>
    <div class="connection-request">
      <div class="overlay" @click="onCancel"></div>
      <div class="request-card">
        <div class="icon">
          <span>💻</span>
        </div>
        
        <h3>Nouvelle connexion MacOS</h3>
        
        <p>
          <strong>{{ deviceName }}</strong> souhaite diffuser de l'audio vers oakOS.
          <span v-if="currentDevice" class="current-device">
            Actuellement connecté à <strong>{{ currentDevice }}</strong>
          </span>
        </p>
        
        <div class="actions">
          <button @click="onAccept" class="accept-button">
            Accepter
          </button>
          <button @click="onReject" class="reject-button">
            Refuser
          </button>
        </div>
      </div>
    </div>
  </template>
  
  <script setup>
  import { computed } from 'vue';
  
  const props = defineProps({
    host: {
      type: String,
      required: true
    },
    deviceName: {
      type: String,
      default: ''
    },
    currentDevice: {
      type: String,
      default: ''
    }
  });
  
  const emit = defineEmits(['accept', 'reject', 'cancel']);
  
  // Nom de périphérique à afficher - utilise l'hôte si aucun nom n'est fourni
  const displayName = computed(() => props.deviceName || props.host);
  
  function onAccept() {
    emit('accept', { host: props.host });
  }
  
  function onReject() {
    emit('reject', { host: props.host });
  }
  
  function onCancel() {
    emit('cancel');
  }
  </script>
  
  <style scoped>
  .connection-request {
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
  
  .icon {
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
  
  .actions {
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