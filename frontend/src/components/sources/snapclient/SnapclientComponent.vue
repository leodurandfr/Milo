<template>
    <div v-if="unifiedStore.currentSource === 'snapclient'" class="snapclient-component">
        <div v-if="unifiedStore.pluginState === 'connected'" class="connected-state">
            <h2>Connecté à MacOS</h2>
            <p>{{ formattedServerName }}</p>
        </div>
        <div v-else-if="unifiedStore.pluginState === 'error'" class="error-state">
            <h2>Erreur de connexion</h2>
            <p>{{ unifiedStore.error }}</p>
        </div>
        <div v-else class="waiting-state">
            <h2>En attente de connexion MacOS</h2>
            <p>Attendez qu'un Mac se connecte via Snapcast...</p>
        </div>
    </div>
</template>

<script setup>
import { computed } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

const unifiedStore = useUnifiedAudioStore();

const formattedServerName = computed(() => {
    const deviceName = unifiedStore.metadata?.device_name;
    if (!deviceName) return 'Serveur inconnu';
    return deviceName
        .replace(/\.local$|\.home$/g, '')
        .replace(/-/g, ' ')
        .replace(/^\w/, c => c.toUpperCase());
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

.error-state {
    text-align: center;
    padding: 1.5rem;
    border: 1px solid #ffcccc;
    border-radius: 4px;
    background-color: #fff0f0;
}
</style>