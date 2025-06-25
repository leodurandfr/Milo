<template>
    <div v-if="unifiedStore.currentSource === 'roc'">
        <div v-if="unifiedStore.pluginState === 'connected'">
            <h2>Connecté au {{ cleanHostname }}</h2>
        </div>
        
        <div v-else-if="unifiedStore.pluginState === 'ready'">
            <h2>En attente de connexion</h2>
        </div>
        
        <div v-else>
            <h2>ROC Audio</h2>
            <p>Démarrage...</p>
        </div>
    </div>
</template>

<script setup>
import { computed } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

const unifiedStore = useUnifiedAudioStore();

const cleanHostname = computed(() => {
    const clientName = unifiedStore.metadata?.client_name;
    if (!clientName) return 'appareil inconnu';
    
    // Nettoyer le nom : enlever .local et remplacer - par espaces
    return clientName
        .replace('.local', '')           // Enlever .local
        .replace(/-/g, ' ');            // Remplacer - par espaces (sans majuscules)
});
</script>

<style scoped>
div {
    text-align: center;
    padding: 2rem;
}

h2 {
    margin: 0 0 1rem 0;
}

p {
    margin: 0.5rem 0;
}
</style>