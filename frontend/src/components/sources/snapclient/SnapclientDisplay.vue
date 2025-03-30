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

// Activer les logs et surveillance
const DEBUG = true;
const lastUpdate = ref(Date.now());

// État dérivé pour contrôler l'affichage
const isConnected = computed(() => {
  const result = snapclientStore.isConnected && snapclientStore.pluginState === 'connected';
  if (DEBUG) console.log(`🔍 Évaluation isConnected: ${result} (pluginState=${snapclientStore.pluginState}, isConnected=${snapclientStore.isConnected})`);
  return result;
});

// Surveillance des changements d'état importants
watch(() => snapclientStore.pluginState, (newState, oldState) => {
  console.log(`⚡ Changement d'état plugin: ${oldState} -> ${newState}`);
  lastUpdate.value = Date.now(); // Force un rafraîchissement
});

watch(() => snapclientStore.isConnected, (newValue, oldValue) => {
  console.log(`⚡ Changement connexion: ${oldValue} -> ${newValue}`);
  lastUpdate.value = Date.now(); // Force un rafraîchissement
});

// Surveiller les changements d'état audio
watch(() => audioStore.currentState, async (newState, oldState) => {
  if (newState === 'macos' && oldState !== 'macos') {
    // Activation de la source MacOS - initialisation unique
    console.log("🔄 Source MacOS activée - Chargement initial de l'état");
    await snapclientStore.fetchStatus(true);
  } else if (oldState === 'macos' && newState !== 'macos') {
    // Désactivation de la source MacOS
    snapclientStore.reset();
  }
});

// Références pour les fonctions de désabonnement
let unsubscribeMonitorConnected = null;
let unsubscribeMonitorDisconnected = null;
let unsubscribeServerEvent = null;
let unsubscribeAudioStatus = null;

onMounted(async () => {
  // Chargement initial unique (pas de polling)
  console.log("🔄 Chargement initial du statut Snapclient");
  await snapclientStore.fetchStatus(true);

  console.log("📡 Abonnement aux événements WebSocket pour Snapclient");

  // Moniteur connecté
  unsubscribeMonitorConnected = on('snapclient_monitor_connected', (data) => {
    console.log("⚡ Moniteur connecté au serveur:", data.host);
    if (audioStore.currentState === 'macos') {
      // En cas de connexion du moniteur, charger le statut complet
      console.log("🔄 Chargement du statut suite à connexion du moniteur");
      snapclientStore.fetchStatus(true);
    }
  });

  // Moniteur déconnecté - mise à jour instantanée
  unsubscribeMonitorDisconnected = on('snapclient_monitor_disconnected', (data) => {
    console.log("⚡ Moniteur déconnecté du serveur:", data.host, data.reason);
    if (audioStore.currentState === 'macos') {
      console.log("🔴 Mise à jour instantanée (sans API): serveur déconnecté");
      snapclientStore.updateFromWebSocketEvent('snapclient_monitor_disconnected', data);
      
      // Force une mise à jour du statut pour synchroniser l'état
      setTimeout(() => snapclientStore.fetchStatus(true), 100);
    }
  });
  
  // Événements serveur
  unsubscribeServerEvent = on('snapclient_server_event', (data) => {
    if (DEBUG) console.log("⚡ Événement serveur reçu:", data);
    
    if (audioStore.currentState === 'macos') {
      // Forcer une mise à jour du statut périodiquement 
      // pour s'assurer que l'interface est synchronisée
      snapclientStore.fetchStatus(true);
    }
  });
  
  // Écouter les mises à jour d'état audio générales
  unsubscribeAudioStatus = on('audio_status_updated', (data) => {
    if (data.source === 'snapclient') {
      console.log("⚡ État audio mis à jour:", data.plugin_state);
      // Force une mise à jour complète à chaque changement d'état
      snapclientStore.updateFromStateEvent(data);
      
      // Force une mise à jour du statut pour synchroniser tous les états
      setTimeout(() => snapclientStore.fetchStatus(true), 100);
    }
  });
});

onUnmounted(() => {
  // Désinscription de tous les événements WebSocket
  if (unsubscribeMonitorConnected) unsubscribeMonitorConnected();
  if (unsubscribeMonitorDisconnected) unsubscribeMonitorDisconnected();
  if (unsubscribeServerEvent) unsubscribeServerEvent();
  if (unsubscribeAudioStatus) unsubscribeAudioStatus();
});
</script>

<style scoped>
.snapclient-display {
  width: 100%;
  padding: 1rem;
}
</style>