<template>
    <!-- Utiliser la condition sur audioStore.currentState au lieu de pluginState -->
    <div v-if="audioStore.currentState === 'macos'" class="snapclient-component">
        <!-- État "ready" - En attente de connexion -->
        <div v-if="!realTimeConnected" class="waiting-state">
            <h2>En attente de connexion MacOS</h2>
            <p>Attendez qu'un Mac se connecte via Snapcast...</p>

            <div class="discovery-section">
                <button @click="discoverServers" :disabled="isDiscovering">
                    {{ isDiscovering ? 'Recherche en cours...' : 'Rechercher des serveurs' }}
                </button>
            </div>

            <!-- Liste des serveurs découverts si disponible -->
            <div v-if="snapclientStore.discoveredServers.length > 0" class="servers-list">
                <h3>Serveurs disponibles:</h3>
                <ul>
                    <li v-for="server in snapclientStore.discoveredServers" :key="server.host"
                        @click="connectToServer(server.host)" class="server-item">
                        {{ server.name }} ({{ server.host }})
                    </li>
                </ul>
            </div>

            <div v-if="snapclientStore.error" class="error-message">
                {{ snapclientStore.error }}
            </div>
        </div>

        <!-- État "connected" - Serveur connecté -->
        <div v-else class="connected-state">
            <h2>Connecté à MacOS</h2>
            <p>{{ formattedServerName }}</p>

            <div class="actions">
                <button @click="disconnect" class="disconnect-button" :disabled="snapclientStore.isLoading">
                    Déconnecter
                </button>
            </div>

            <div v-if="snapclientStore.error" class="error-message">
                {{ snapclientStore.error }}
            </div>
        </div>
    </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { useSnapclientStore } from '@/stores/snapclient';
import { useAudioStore } from '@/stores/index';
import useWebSocket from '@/services/websocket';
import axios from 'axios';

const snapclientStore = useSnapclientStore();
const audioStore = useAudioStore();
const { on, isConnected: wsConnected } = useWebSocket();

// États locaux simplifiés
const isDiscovering = ref(false);
let reconnectTimer = null;
let connectionCheckTimer = null;
const wsUnsubscribers = [];

// État réactif pour la connexion, indépendant du store
const realTimeConnected = ref(snapclientStore.isConnected);

// Connecter à un serveur spécifique
async function connectToServer(host) {
    try {
        isDiscovering.value = true;
        await snapclientStore.connectToServer(host);
        realTimeConnected.value = snapclientStore.isConnected;
    } catch (err) {
        console.error('Erreur de connexion:', err);
    } finally {
        isDiscovering.value = false;
    }
}

// Vérifier la connexion directement avec l'API
async function checkConnectionStatus() {
    if (audioStore.currentState !== 'macos') return;
    
    try {
        // Ajouter un timestamp pour éviter le cache
        const response = await axios.get(`/api/snapclient/status?_t=${Date.now()}`);
        const newStatus = response.data.device_connected === true;
        
        // Mise à jour de notre état local réactif
        if (realTimeConnected.value !== newStatus) {
            console.log(`🔄 État de connexion changé: ${realTimeConnected.value} -> ${newStatus}`);
            realTimeConnected.value = newStatus;
            
            // Si déconnecté, mettre à jour le store aussi
            if (!newStatus && snapclientStore.isConnected) {
                snapclientStore.forceDisconnect("verification_status");
            }
            // Si connecté, mettre à jour le store aussi
            if (newStatus && !snapclientStore.isConnected) {
                snapclientStore.updateConnectionState(true, {
                    device_name: response.data.device_name,
                    host: response.data.host
                });
            }
        }
    } catch (error) {
        console.error("Erreur lors de la vérification de l'état:", error);
    }
}

// Observer les changements dans le store
watch(() => snapclientStore.isConnected, (newValue) => {
    console.log(`🔄 isConnected changé dans le store: ${newValue}`);
    realTimeConnected.value = newValue;
});

// Formater le nom du serveur
const formattedServerName = computed(() => {
    if (!snapclientStore.deviceName) return 'Serveur inconnu';
    const name = snapclientStore.deviceName.replace(/\.local$|\.home$/g, '');
    return name.charAt(0).toUpperCase() + name.slice(1);
});

// Découvrir les serveurs
async function discoverServers() {
    if (isDiscovering.value) return;

    try {
        isDiscovering.value = true;
        const result = await snapclientStore.discoverServers();
        console.log("Serveurs découverts:", result);
    } catch (err) {
        console.error('Erreur lors de la découverte des serveurs:', err);
    } finally {
        isDiscovering.value = false;
    }
}

// Se déconnecter du serveur
async function disconnect() {
    try {
        await snapclientStore.disconnectFromServer();
        realTimeConnected.value = false;
    } catch (err) {
        console.error('Erreur de déconnexion:', err);
        realTimeConnected.value = false;
    }
}

// Configurer les écouteurs d'événements WebSocket
function setupWebSocketEvents() {
    // Événements critiques - attention au log pour le debug
    ['snapclient_monitor_connected', 'snapclient_monitor_disconnected', 'snapclient_server_disappeared', 'snapclient_server_discovered'].forEach(eventType => {
        const unsub = on(eventType, (data) => {
            console.log(`⚡ Événement WebSocket reçu: ${eventType}`, data);
            
            // Mise à jour directe de notre état réactif
            if (eventType === 'snapclient_monitor_disconnected' || eventType === 'snapclient_server_disappeared') {
                console.log('🔌 Déconnexion détectée via WebSocket');
                realTimeConnected.value = false;
            } else if (eventType === 'snapclient_monitor_connected') {
                console.log('🔌 Connexion détectée via WebSocket');
                realTimeConnected.value = true;
            }
            
            // Mettre à jour le store (vérifie si le WebSocket est connecté)
            if (wsConnected.value) {
                snapclientStore.updateFromWebSocketEvent(eventType, data);
                
                // Vérifier l'état réel dans tous les cas
                setTimeout(checkConnectionStatus, 100);
            }
        });
        wsUnsubscribers.push(unsub);
    });

    // Mettre à jour l'état
    const unsubAudio = on('audio_status_updated', (data) => {
        if (data.source === 'snapclient') {
            console.log('📊 État audio mis à jour:', data);
            snapclientStore.updateFromStateEvent(data);
            
            // Synchroniser notre état local avec les mises à jour d'état
            if (data.plugin_state === 'ready' || data.plugin_state === 'inactive') {
                realTimeConnected.value = false;
            } else if (data.plugin_state === 'connected' && data.connected === true) {
                realTimeConnected.value = true;
            }
            
            // Vérifier l'état réel
            setTimeout(checkConnectionStatus, 100);
        }
    });
    wsUnsubscribers.push(unsubAudio);
}

onMounted(async () => {
    // Récupérer le statut initial et forcer le rafraîchissement des données
    console.log('📊 Composant monté, récupération du statut initial');
    await snapclientStore.fetchStatus(true);
    
    // Initialiser notre état local réactif
    realTimeConnected.value = snapclientStore.isConnected;
    console.log(`État initial: ${realTimeConnected.value ? 'Connecté' : 'Déconnecté'}`);

    // Configurer les événements WebSocket
    setupWebSocketEvents();

    // Lancer une découverte initiale
    discoverServers();

    // Configurer une détection périodique tant qu'on n'est pas connecté
    reconnectTimer = setInterval(() => {
        if (audioStore.currentState === 'macos' && !realTimeConnected.value) {
            console.log('🔄 Détection périodique des serveurs');
            discoverServers();
        }
    }, 10000); // Toutes les 10 secondes
    
    // Vérification périodique de l'état de connexion réel
    connectionCheckTimer = setInterval(() => {
        if (audioStore.currentState === 'macos') {
            checkConnectionStatus();
        }
    }, 5000); // Toutes les 5 secondes
});

onUnmounted(() => {
    // Nettoyer les intervalles
    if (reconnectTimer) clearInterval(reconnectTimer);
    if (connectionCheckTimer) clearInterval(connectionCheckTimer);

    // Nettoyer les abonnements WebSocket
    wsUnsubscribers.forEach(unsub => unsub && unsub());
});
</script>

<style scoped>
.snapclient-component {
  max-width: 500px;
  margin: 0 auto;
  padding: 1rem;
}

.waiting-state, .connected-state {
  text-align: center;
  padding: 1.5rem;
  border: 1px solid #ddd;
  border-radius: 4px;
  background-color: #f9f9f9;
}

.discovery-section, .actions {
  margin-top: 1rem;
}

.servers-list {
  margin-top: 1rem;
  text-align: left;
}

.server-item {
  padding: 0.5rem;
  margin: 0.25rem 0;
  background-color: #eee;
  border-radius: 4px;
  cursor: pointer;
  transition: background-color 0.2s;
}

.server-item:hover {
  background-color: #ddd;
}

button {
  background-color: #2196F3;
  color: white;
  border: none;
  border-radius: 4px;
  padding: 8px 16px;
  cursor: pointer;
  transition: background-color 0.2s;
}

button:hover:not(:disabled) {
  background-color: #0b7dda;
}

button:disabled {
  background-color: #ccc;
  cursor: not-allowed;
}

.disconnect-button {
  background-color: #e74c3c;
}

.disconnect-button:hover:not(:disabled) {
  background-color: #c0392b;
}

.error-message {
  background-color: #e74c3c;
  color: white;
  padding: 0.5rem;
  margin-top: 1rem;
  border-radius: 4px;
}
</style>