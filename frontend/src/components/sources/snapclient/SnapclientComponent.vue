<template>
    <div v-if="audioStore.currentState === 'macos'" class="snapclient-component">
        <div v-if="!isConnected" class="waiting-state">
            <h2>En attente de connexion MacOS</h2>
            <p>Attendez qu'un Mac se connecte via Snapcast...</p>
        </div>
        <div v-else class="connected-state">
            <h2>Connecté à MacOS</h2>
            <p>{{ formattedServerName }}</p>
            <div class="actions">
                <button @click="disconnect" class="disconnect-button" :disabled="snapclientStore.isLoading">
                    Déconnecter
                </button>
            </div>
        </div>
    </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted } from 'vue';
import { useSnapclientStore } from '@/stores/snapclient';
import { useAudioStore } from '@/stores/index';
import useWebSocket from '@/services/websocket';

const snapclientStore = useSnapclientStore();
const audioStore = useAudioStore();
const { on } = useWebSocket();
const isConnected = computed(() => snapclientStore.isConnected);
const formattedServerName = computed(() => {
    if (!snapclientStore.deviceName) return 'Serveur inconnu';
    const name = snapclientStore.deviceName
        .replace(/\.local$|\.home$/g, '')
        .replace(/-/g, ' ');
    return name.charAt(0).toUpperCase() + name.slice(1);
});

async function disconnect() {
    try {
        await snapclientStore.disconnectFromServer();
    } catch (err) {
        console.error('Erreur de déconnexion:', err);
    }
}

function setupWebSocketEvents() {
    const events = [
        'snapclient_monitor_connected',
        'snapclient_monitor_disconnected',
        'snapclient_server_disappeared',
        'snapclient_server_discovered'
    ];

    const unsubscribers = events.map(event =>
        on(event, data => snapclientStore.updateFromWebSocketEvent(event, data))
    );

    unsubscribers.push(
        on('audio_status_updated', data => {
            if (data.source === 'snapclient') {
                snapclientStore.updateFromStateEvent(data);
            }
        })
    );

    return () => unsubscribers.forEach(unsub => unsub && unsub());
}

onMounted(async () => {
    await snapclientStore.fetchStatus(true);
    const cleanup = setupWebSocketEvents();
    onUnmounted(cleanup);
});
</script>

<style scoped>
.snapclient-component {
    max-width: 500px;
    margin: 0 auto;
    padding: 1rem;
}

.waiting-state,
.connected-state {
    text-align: center;
    padding: 1.5rem;
    border: 1px solid #ddd;
    border-radius: 4px;
    background-color: #f9f9f9;
}

.actions {
    margin-top: 1rem;
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
</style>