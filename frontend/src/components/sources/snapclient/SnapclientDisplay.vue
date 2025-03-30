<template>
  <div class="snapclient-display">
    <SnapclientWaitingConnection v-if="!isConnected" />
    <SnapclientConnectionInfo v-else />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, watch, ref } from 'vue';
import { useAudioStore } from '@/stores/index';
import { useSnapclientStore } from '@/stores/snapclient';
import SnapclientWaitingConnection from './SnapclientWaitingConnection.vue';
import SnapclientConnectionInfo from './SnapclientConnectionInfo.vue';
import useWebSocket from '@/services/websocket';

const { on } = useWebSocket();
const audioStore = useAudioStore();
const snapclientStore = useSnapclientStore();

// Activer les logs d'événements WebSocket détaillés
const DEBUG = true;

// État dérivé pour contrôler l'affichage
const isConnected = computed(() =>
  snapclientStore.isConnected &&
  snapclientStore.pluginState === 'connected'
);

// Surveiller les changements d'état audio
watch(() => audioStore.currentState, async (newState, oldState) => {
  if (newState === 'macos' && oldState !== 'macos') {
    // Activation de la source MacOS - initialisation unique
    console.log("🔄 Source MacOS activée - Chargement initial de l'état");
    await snapclientStore.fetchStatus();
  } else if (oldState === 'macos' && newState !== 'macos') {
    // Désactivation de la source MacOS
    snapclientStore.reset();
  }
});

// Références pour les fonctions de désabonnement
let unsubscribeMonitorConnected = null;
let unsubscribeMonitorDisconnected = null;
let unsubscribeServerEvent = null;
let unsubscribeServerDisappeared = null;
let unsubscribeAudioStatus = null;

onMounted(async () => {
  // Chargement initial unique (pas de polling)
  console.log("🔄 Chargement initial du statut Snapclient");
  await snapclientStore.fetchStatus();

  if (DEBUG) console.log("📡 Abonnement aux événements WebSocket pour Snapclient");

  // Moniteur connecté
  unsubscribeMonitorConnected = on('snapclient_monitor_connected', (data) => {
    console.log("⚡ Moniteur connecté au serveur:", data.host);
    if (audioStore.currentState === 'macos') {
      // En cas de connexion du moniteur, charger le statut complet
      console.log("🔄 Chargement du statut suite à connexion du moniteur");
      snapclientStore.fetchStatus();
    }
  });

  // Moniteur déconnecté - mise à jour instantanée
  unsubscribeMonitorDisconnected = on('snapclient_monitor_disconnected', (data) => {
    console.log("⚡ Moniteur déconnecté du serveur:", data.host, data.reason);
    if (audioStore.currentState === 'macos') {
      console.log("🔴 Mise à jour instantanée (sans API): serveur déconnecté");
      snapclientStore.updateFromWebSocketEvent('snapclient_monitor_disconnected', data);
    }
  });
  
  // Événements serveur
  unsubscribeServerEvent = on('snapclient_server_event', (data) => {
    if (DEBUG) console.log("⚡ Événement serveur reçu:", data);
    
    if (audioStore.currentState === 'macos') {
      // Analyser l'événement pour mettre à jour l'état si nécessaire
      const methodName = data?.data?.method || data?.method;
      
      if (methodName === "Server.OnUpdate") {
        console.log("🔄 Changement détecté sur le serveur");
        snapclientStore.fetchStatus();
      }
    }
  });
  
  // Disparition du serveur - mise à jour instantanée
  unsubscribeServerDisappeared = on('snapclient_server_disappeared', (data) => {
    console.log("⚡ Serveur disparu:", data.host);
    if (audioStore.currentState === 'macos') {
      console.log("🔴 Mise à jour instantanée (sans API): serveur disparu");
      snapclientStore.updateFromWebSocketEvent('snapclient_server_disappeared', data);
    }
  });
  
  // Écouter les mises à jour d'état audio générales
  unsubscribeAudioStatus = on('audio_status_updated', (data) => {
    if (data.source === 'snapclient') {
      console.log("⚡ État audio mis à jour:", data.plugin_state);
      snapclientStore.updateFromStateEvent(data);
    }
  });
});

onUnmounted(() => {
  // Désinscription de tous les événements WebSocket
  if (unsubscribeMonitorConnected) unsubscribeMonitorConnected();
  if (unsubscribeMonitorDisconnected) unsubscribeMonitorDisconnected();
  if (unsubscribeServerEvent) unsubscribeServerEvent();
  if (unsubscribeServerDisappeared) unsubscribeServerDisappeared();
  if (unsubscribeAudioStatus) unsubscribeAudioStatus();
});
</script>

<style scoped>
.snapclient-display {
  width: 100%;
  padding: 1rem;
}
</style>