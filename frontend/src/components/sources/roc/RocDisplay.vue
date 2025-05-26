<template>
    <div v-if="unifiedStore.currentSource === 'roc'" class="roc-component">
        <div v-if="unifiedStore.pluginState === 'connected'" class="connected-state">
            <h2>ROC Audio - Connecté</h2>
            <div v-if="clientName" class="client-info">
                <h3>{{ clientName }}</h3>
                <p>Audio en streaming depuis Mac</p>
            </div>
            <div class="connection-info">
                <p>Port RTP: {{ rocInfo.rtp_port || 10001 }}</p>
                <p>Sortie: {{ rocInfo.audio_output || 'hw:1,0' }}</p>
            </div>
        </div>
        
        <div v-else-if="unifiedStore.pluginState === 'ready'" class="ready-state">
            <h2>ROC Audio - En écoute</h2>
            <p>Prêt à recevoir l'audio depuis votre Mac</p>
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
            <h2>ROC Audio</h2>
            <p>Service en cours de démarrage...</p>
        </div>
    </div>
</template>

<script setup>
import { computed } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import axios from 'axios';

const unifiedStore = useUnifiedAudioStore();

const rocInfo = computed(() => {
    return unifiedStore.metadata || {};
});

const clientName = computed(() => {
    return rocInfo.value.client_name || rocInfo.value.client_ip || null;
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
.ready-state,
.connected-state {
    text-align: center;
    padding: 2rem;
    border: 1px solid #ddd;
    border-radius: 8px;
    background-color: #f9f9f9;
}

.ready-state {
    border-color: #17a2b8;
    background-color: #f0f8ff;
}

.connected-state {
    border-color: #28a745;
    background-color: #f0fff4;
}

.error-state {
    text-align: center;
    padding: 2rem;
    border: 1px solid #ffcccc;
    border-radius: 8px;
    background-color: #fff0f0;
}

.client-info {
    margin: 1.5rem 0;
    padding: 1rem;
    background-color: rgba(40, 167, 69, 0.1);
    border-radius: 8px;
}

.client-info h3 {
    margin: 0 0 0.5rem 0;
    color: #28a745;
    font-size: 1.5em;
}

.connection-info {
    margin-top: 1.5rem;
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