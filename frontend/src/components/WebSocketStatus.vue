<!-- frontend/src/components/WebSocketStatus.vue -->
<template>
    <div class="ws-status" :class="{ 'connected': isConnected, 'disconnected': !isConnected }">
      <div class="indicator"></div>
      <span v-if="isConnected">Connecté au serveur</span>
      <span v-else>Déconnecté <button v-if="!isConnected" @click="reconnect" class="reconnect-btn">Reconnecter</button></span>
    </div>
  </template>
  
  <script setup>
  import useWebSocket from '@/services/websocket';
  
  const { isConnected, reconnect } = useWebSocket();
  </script>
  
  <style scoped>
  .ws-status {
    position: fixed;
    bottom: 10px;
    right: 10px;
    padding: 5px 10px;
    border-radius: 4px;
    font-size: 12px;
    display: flex;
    align-items: center;
    gap: 5px;
    z-index: 1000;
    background-color: rgba(255, 255, 255, 0.8);
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
  }
  
  .indicator {
    width: 8px;
    height: 8px;
    border-radius: 50%;
  }
  
  .connected .indicator {
    background-color: #4CAF50;
  }
  
  .disconnected .indicator {
    background-color: #F44336;
  }
  
  .reconnect-btn {
    border: none;
    background-color: #2196F3;
    color: white;
    padding: 2px 5px;
    border-radius: 3px;
    cursor: pointer;
    font-size: 10px;
    margin-left: 5px;
  }
  
  .reconnect-btn:hover {
    background-color: #0b7dda;
  }
  </style>