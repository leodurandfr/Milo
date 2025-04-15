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

            <!-- Afficher des informations de débogage en dev -->
            <div v-if="isDev" class="debug-info">
                <p>État du plugin: {{ snapclientStore.pluginState }}</p>
                <p>isConnected (store): {{ snapclientStore.isConnected }}</p>
                <p>isConnected (réactif): {{ realTimeConnected }}</p>
                <p>Serveurs trouvés: {{ snapclientStore.discoveredServers.length }}</p>
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

// Définir une constante pour le mode dev
const isDev = import.meta.env.DEV;

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
        }
    } catch (error) {
        console.error("Erreur lors de la vérification de l'état:", error);
        // En cas d'erreur, considérer comme déconnecté
        realTimeConnected.value = false;
    }
}

// Observer les changements dans le store
watch(() => snapclientStore.isConnected, (newValue) => {
    realTimeConnected.value = newValue;
});

// Formater le nom du serveur
const formattedServerName = computed(() => {
    if (!snapclientStore.deviceName) return 'Serveur inconnu';

    // Nettoyer et formater le nom
    const name = snapclientStore.deviceName
        .replace(/\.local$|\.home$/g, '');

    return name.charAt(0).toUpperCase() + name.slice(1);
});

// Découvrir les serveurs
async function discoverServers() {
    if (isDiscovering.value) return;

    try {
        isDiscovering.value = true;

        // Découvrir les serveurs
        const result = await snapclientStore.discoverServers();

        if (!result.servers || result.servers.length === 0) {
            console.log("Aucun serveur trouvé");
            return;
        }

        // Tenter de se connecter au dernier serveur connu
        const lastServer = localStorage.getItem('lastSnapclientServer');
        if (lastServer) {
            try {
                const serverData = JSON.parse(lastServer);
                // Vérifier si le dernier serveur est toujours disponible
                const foundServer = result.servers.find(s => s.host === serverData.host);

                if (foundServer) {
                    console.log("Reconnexion au dernier serveur:", serverData.host);
                    await snapclientStore.connectToServer(serverData.host);
                    return;
                }
            } catch (e) {
                console.error("Erreur de parsing du dernier serveur:", e);
            }
        }

        // Si un seul serveur est disponible, s'y connecter automatiquement
        if (result.servers.length === 1) {
            const server = result.servers[0];
            console.log("Un seul serveur disponible, connexion automatique:", server.name);
            await snapclientStore.connectToServer(server.host);
        }
    } catch (err) {
        console.error("Erreur lors de la découverte/connexion:", err);
    } finally {
        isDiscovering.value = false;
    }
}

// Se déconnecter du serveur
async function disconnect() {
    try {
        await snapclientStore.disconnectFromServer();
        // Mettre à jour immédiatement notre état local aussi
        realTimeConnected.value = false;
    } catch (err) {
        console.error('Erreur de déconnexion:', err);
        // Forcer la mise à jour de l'état en cas d'erreur
        realTimeConnected.value = false;
    }
}

// Gérer les événements critiques spécifiques
function handleCriticalEvent(event) {
    console.warn("🔥 Événement critique reçu via DOM", event.detail);
    
    if (audioStore.currentState === 'macos' && 
        (event.detail.type === 'snapclient_monitor_disconnected' || 
         event.detail.type === 'snapclient_server_disappeared')) {
        // Mettre à jour notre état local immédiatement
        realTimeConnected.value = false;
        
        // Forcer la mise à jour du store
        snapclientStore.forceDisconnect(event.detail.type);
        
        // Vérifier l'état réel auprès de l'API
        checkConnectionStatus();
    }
}

// Configurer les écouteurs d'événements WebSocket
function setupWebSocketEvents() {
    // Événements critiques
    ['snapclient_monitor_connected', 'snapclient_monitor_disconnected', 'snapclient_server_disappeared'].forEach(eventType => {
        const unsub = on(eventType, (data) => {
            console.log(`⚡ Événement WebSocket reçu: ${eventType}`, data);
            
            // Mise à jour directe de notre état réactif
            if (eventType === 'snapclient_monitor_disconnected' || eventType === 'snapclient_server_disappeared') {
                realTimeConnected.value = false;
            } else if (eventType === 'snapclient_monitor_connected') {
                realTimeConnected.value = true;
            }
            
            // Mettre à jour le store
            snapclientStore.updateFromWebSocketEvent(eventType, data);
        });
        wsUnsubscribers.push(unsub);
    });

    // Mettre à jour l'état
    const unsubAudio = on('audio_status_updated', (data) => {
        if (data.source === 'snapclient') {
            snapclientStore.updateFromStateEvent(data);
            // Synchroniser notre état local avec les mises à jour d'état
            if (data.plugin_state === 'ready' || data.plugin_state === 'inactive') {
                realTimeConnected.value = false;
            } else if (data.plugin_state === 'connected' && data.connected === true) {
                realTimeConnected.value = true;
            }
        }
    });
    wsUnsubscribers.push(unsubAudio);

    // Ajouter un écouteur pour les événements globaux de déconnexion
    const handleGlobalDisconnect = (event) => {
        console.log("🔌 Événement global de déconnexion reçu", event.detail);
        // Mettre à jour notre état local
        realTimeConnected.value = false;
        // Rafraîchir immédiatement le statut
        snapclientStore.fetchStatus(true);
    };
    
    document.addEventListener('snapclient-disconnected', handleGlobalDisconnect);
    wsUnsubscribers.push(() => {
        document.removeEventListener('snapclient-disconnected', handleGlobalDisconnect);
    });
    
    // Ajouter l'écouteur pour les événements critiques
    window.addEventListener('snapclient-critical-event', handleCriticalEvent);
    wsUnsubscribers.push(() => {
        window.removeEventListener('snapclient-critical-event', handleCriticalEvent);
    });
}

onMounted(async () => {
    // Récupérer le statut initial
    await snapclientStore.fetchStatus(true);
    
    // Initialiser notre état local réactif
    realTimeConnected.value = snapclientStore.isConnected;

    // Configurer les événements WebSocket
    setupWebSocketEvents();

    // Lancer une découverte initiale
    discoverServers();

    // Configurer une détection périodique tant qu'on n'est pas connecté
    reconnectTimer = setInterval(() => {
        if (audioStore.currentState === 'macos' && !realTimeConnected.value) {
            discoverServers();
        }
    }, 10000); // Toutes les 10 secondes
    
    // Vérification périodique légère de l'état de connexion réel
    connectionCheckTimer = setInterval(() => {
        if (audioStore.currentState === 'macos') {
            checkConnectionStatus();
        }
    }, 5000); // Toutes les 5 secondes
});

onUnmounted(() => {
    // Nettoyer les intervalles
    if (reconnectTimer) {
        clearInterval(reconnectTimer);
    }
    
    if (connectionCheckTimer) {
        clearInterval(connectionCheckTimer);
    }

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

.debug-info {
  margin-top: 1rem;
  padding: 0.5rem;
  border: 1px dashed #aaa;
  background-color: #f0f0f0;
  font-size: 0.8rem;
  text-align: left;
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