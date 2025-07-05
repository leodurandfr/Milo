<!-- frontend/src/components/ui/PluginStatus.vue - Version avec état starting -->
<template>
    <div class="plugin-status">
        <div class="plugin-status-content">
            <div class="plugin-status-inner">
                <!-- Section info appareil -->
                <div class="device-info">
                    <div class="device-info-content">
                        <div class="device-info-inner">
                            <!-- Icône du plugin -->
                            <div class="plugin-icon">
                                <AppIcon :name="iconName" :size="32"
                                    :state="pluginState === 'starting' ? 'loading' : 'normal'" />
                            </div>

                            <!-- Statut textuel -->
                            <div class="device-status">
                                <div v-if="statusLines.length === 1" class="status-single">
                                    <h2 class="heading-2">{{ statusLines[0] }}</h2>
                                </div>
                                <template v-else>
                                    <div class="status-line-1">
                                        <h2 class="heading-2">{{ statusLines[0] }}</h2>
                                    </div>
                                    <div class="status-line-2">
                                        <h2 class="heading-2">{{ statusLines[1] }}</h2>
                                    </div>
                                </template>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Bouton déconnecter (conditionnel) -->
                <div v-if="showDisconnectButton" class="disconnect-button">
                    <div class="disconnect-button-content">
                        <div class="disconnect-button-inner">
                            <button @click="handleDisconnect" :disabled="isDisconnecting" class="disconnect-text">
                                <p>{{ isDisconnecting ? 'Déconnexion...' : 'Déconnecter' }}</p>
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</template>

<script setup>
import { computed } from 'vue';
import AppIcon from './AppIcon.vue';

// Props
const props = defineProps({
    pluginType: {
        type: String,
        required: true,
        validator: (value) => ['librespot', 'bluetooth', 'roc'].includes(value)
    },
    pluginState: {
        type: String,
        required: true
    },
    deviceName: {
        type: String,
        default: ''
    },
    isDisconnecting: {
        type: Boolean,
        default: false
    }
});

// Émissions
const emit = defineEmits(['disconnect']);

// Nom de l'icône selon le plugin
const iconName = computed(() => {
    // Mapping librespot -> spotify pour l'icône
    return props.pluginType === 'librespot' ? 'spotify' : props.pluginType;
});

// Lignes de statut selon l'état
const statusLines = computed(() => {
    // État de démarrage (nouveau)
    if (props.pluginState === 'starting') {
        switch (props.pluginType) {
            case 'bluetooth':
                return ['Démarrage du', 'Bluetooth'];
            case 'roc':
                return ['Démarrage de la', 'Réception audio Mac'];
            case 'librespot':
                return ['Démarrage de', 'Spotify'];
            default:
                return ['Démarrage...'];
        }
    }

    // État ready : messages d'attente
    if (props.pluginState === 'ready') {
        switch (props.pluginType) {
            case 'bluetooth':
                return ['oakOS est visible dans vos accessoires Bluetooth'];
            case 'roc':
                return ['oakOS est prêt à recevoir l\'audio d\'un ordinateur Mac'];
            case 'librespot':
                return ['oakOS est visible dans vos appareils Spotify'];
            default:
                return ['En attente de connexion'];
        }
    }

    // État connected : messages avec nom d'appareil
    if (props.pluginState === 'connected' && props.deviceName) {
        switch (props.pluginType) {
            case 'bluetooth':
                return ['Connecté à', props.deviceName];
            case 'roc':
                return ['Connecté au', props.deviceName];
            default:
                return ['Connecté à', props.deviceName];
        }
    }

    return ['État inconnu'];
});

// Affichage du bouton déconnecter
const showDisconnectButton = computed(() => {
    // Pas de bouton pendant le démarrage
    if (props.pluginState === 'starting') {
        return false;
    }

    return props.pluginType === 'bluetooth' && props.pluginState === 'connected';
});

// Gestionnaire de déconnexion
function handleDisconnect() {
    emit('disconnect');
}
</script>

<style scoped>
.plugin-status {
    background: var(--color-background-neutral);
    border-radius: var(--radius-06);
    box-shadow: var(--shadow-02);
    width: 364px;
    position: relative;
    margin: auto;
}

.plugin-status-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    position: relative;
    height: 100%;
}

.plugin-status-inner {
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    gap: var(--space-02);
    align-items: center;
    justify-content: flex-start;
    padding: var(--space-06) var(--space-04) var(--space-04) var(--space-04);
    position: relative;
    width: 100%;
    height: 100%;
}

.device-info {
    position: relative;
    flex-shrink: 0;
    width: 100%;
}

.device-info-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    position: relative;
    width: 100%;
    height: 100%;
}

.device-info-inner {
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    gap: var(--space-04);
    align-items: center;
    justify-content: flex-start;
    padding: 0 var(--space-04) var(--space-04) var(--space-04);
    position: relative;
    width: 100%;
}

/* Icône du plugin */
.plugin-icon {
    position: relative;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
}

/* Statut textuel */
.device-status {
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
}

.status-single h2 {
    color: var(--color-text);
}

.status-line-1 h2 {
    color: var(--color-text-secondary);
}

.status-line-2 h2 {
    color: var(--color-text);
}

/* Bouton déconnecter */
.disconnect-button {
    background: var(--color-background-strong);
    height: 42px;
    position: relative;
    border-radius: var(--radius-04);
    flex-shrink: 0;
    width: 100%;
}

.disconnect-button-content {
    display: flex;
    flex-direction: row;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    position: relative;
    width: 100%;
    height: 100%;
}

.disconnect-button-inner {
    box-sizing: border-box;
    display: flex;
    flex-direction: row;
    gap: 40px;
    height: 42px;
    align-items: center;
    justify-content: center;
    padding: var(--space-02) var(--space-05);
    position: relative;
    width: 100%;
}

.disconnect-text {
    background: none;
    border: none;
    cursor: pointer;
    font-family: 'Neue Montreal Medium';
    font-weight: 500;
    line-height: 0;
    font-style: normal;
    position: relative;
    flex-shrink: 0;
    color: var(--color-text-secondary);
    font-size: var(--font-size-body);
    text-align: center;
    white-space: nowrap;
    letter-spacing: var(--letter-spacing-sans-serif);
}

.disconnect-text:hover:not(:disabled) {
    color: var(--color-text);
}

.disconnect-text:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

.disconnect-text p {
    display: block;
    line-height: var(--line-height-body);
    white-space: pre;
    margin: 0;
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
    .plugin-status {
        width: 100%;
        max-width: 348px;
    }
}
</style>