<template>
    <div class="snapclient-display">
        <!-- Attente de connexion -->
        <SnapclientWaitingConnection v-if="!isConnected" />

        <!-- Connecté -->
        <SnapclientConnectionInfo v-else />
    </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, watch } from 'vue';
import { useAudioStore } from '@/stores/index';
import { useSnapclientStore } from '@/stores/snapclient';
import SnapclientWaitingConnection from './SnapclientWaitingConnection.vue';
import SnapclientConnectionInfo from './SnapclientConnectionInfo.vue';

const audioStore = useAudioStore();
const snapclientStore = useSnapclientStore();

// États dérivés pour contrôler l'affichage
const isConnected = computed(() =>
    snapclientStore.isConnected &&
    snapclientStore.pluginState === 'connected'
);

// Surveiller les changements d'état audio
watch(() => audioStore.currentState, async (newState, oldState) => {
    if (newState === 'macos' && oldState !== 'macos') {
        // Activation de la source MacOS
        await snapclientStore.fetchStatus();
    } else if (oldState === 'macos' && newState !== 'macos') {
        // Désactivation de la source MacOS
        snapclientStore.reset();
    }
});

// Configurer une vérification périodique du statut
let statusInterval = null;
onMounted(() => {
    statusInterval = setInterval(async () => {
        if (audioStore.currentState === 'macos') {
            try {
                console.log('Vérification périodique du statut snapclient...');
                await snapclientStore.fetchStatus();

                // Si nous ne sommes plus connectés mais que l'UI montre toujours une connexion,
                // forcer une mise à jour complète
                if (!snapclientStore.isConnected && isConnected.value) {
                    console.log('État de connexion incohérent détecté, forçage de mise à jour...');
                    location.reload(); // Dernier recours si l'état UI n'est pas synchronisé
                }
            } catch (err) {
                console.error('Erreur lors de la vérification du statut:', err);
            }
        }
    }, 3000); // Vérification plus fréquente toutes les 3 secondes
});

// S'assurer que le store est initialisé avec les données correctes
onMounted(async () => {
    await snapclientStore.fetchStatus();
    // Démarrer l'intervalle de rafraîchissement automatique
    snapclientStore.startRefreshInterval();
});

// Nettoyer au démontage
onUnmounted(() => {
    // Arrêter l'intervalle
    if (statusInterval) {
        clearInterval(statusInterval);
    }

    // Arrêter l'intervalle de rafraîchissement du store
    snapclientStore.stopRefreshInterval();

    // Réinitialiser l'état si on quitte sans changer de source
    if (audioStore.currentState !== 'macos') {
        snapclientStore.reset();
    }
});
</script>

<style scoped>
.snapclient-display {
    width: 100%;
    padding: 1rem;
}
</style>