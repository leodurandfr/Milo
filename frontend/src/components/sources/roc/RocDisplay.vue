<template>
    <div v-if="unifiedStore.currentSource === 'roc'" class="roc-component">
        <div v-if="unifiedStore.pluginState === 'ready'" class="connected-state">
            <h2>ROC Audio Actif</h2>
            <p>En écoute sur le réseau</p>
            <div class="connection-info">
                <p>Port RTP: {{ rocInfo.rtp_port || 10001 }}</p>
                <p>Sortie: {{ rocInfo.audio_output || 'hw:1,0' }}</p>
            </div>
        </div>
        <div v-else-if="unifiedStore.pluginState === 'error'" class="error-state">
            <h2>Erreur ROC</h2>
            <p>{{ unifiedStore.error || 'Erreur de connexion' }}</p>
            <button @click="restartService" class="restart-btn">Redémarrer</button>
        </div>
        <div v-else class="waiting-state">
            <h2>ROC Audio Inactif</h2>
            <p>Service en cours de démarrage...</p>
        </div>
    </div>
</template>

<script setup>
import { computed, ref } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import axios from 'axios';

const unifiedStore = useUnifiedAudioStore();

const rocInfo = computed(() => {
    return unifiedStore.metadata || {};
});

async function restartService() {
    try {
        await axios.post('/roc/restart');
    } catch (error) {
        console.error('Erreur redémarrage ROC:', error);
    }
}
</script>

<style scoped>
.roc-component {
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

.error-state {
    text-align: center;
    padding: 1.5rem;
    border: 1px solid #ffcccc;
    border-radius: 4px;
    background-color: #fff0f0;
}

.connection-info {
    margin-top: 1rem;
    font-size: 0.9em;
    color: #666;
}

.connection-info p {
    margin: 0.25rem 0;
}

.restart-btn {
    margin-top: 1rem;
    padding: 0.5rem 1rem;
    background-color: #007bff;
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
}

.restart-btn:hover {
    background-color: #0056b3;
}
</style>