<!-- PluginStatus.vue - Version corrigée pour animation d'entrée fiable -->
<template>
    <div ref="containerRef" class="plugin-status" :class="[`state-${animationState}`]">
        <div class="plugin-status-content">
            <div class="plugin-status-inner">
                <!-- Section info appareil -->
                <div class="device-info">
                    <div class="device-info-content">
                        <div class="device-info-inner">
                            <!-- Icône du plugin -->
                            <div class="plugin-icon">
                                <AppIcon :name="displayedIconName" :size="32"
                                    :state="displayedPluginState === 'starting' ? 'loading' : 'normal'" />
                            </div>

                            <!-- Statut textuel -->
                            <div class="device-status">
                                <div v-if="displayedStatusLines.length === 1" class="status-single">
                                    <h2 class="heading-2">{{ displayedStatusLines[0] }}</h2>
                                </div>
                                <template v-else>
                                    <div class="status-line-1" :class="getDisplayedStatusLine1Class()">
                                        <h2 class="heading-2">{{ displayedStatusLines[0] }}</h2>
                                    </div>
                                    <div class="status-line-2" :class="getDisplayedStatusLine2Class()">
                                        <h2 class="heading-2">{{ displayedStatusLines[1] }}</h2>
                                    </div>
                                </template>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Bouton déconnecter (conditionnel) -->
                <div v-if="displayedShowDisconnectButton" class="disconnect-button">
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
import { computed, ref, watch, nextTick } from 'vue';
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
    },
    shouldAnimate: {
        type: Boolean,
        default: false
    }
});

// Émissions
const emit = defineEmits(['disconnect']);

// === ÉTATS INTERNES POUR LE TIMING ===
const animationState = ref('hidden'); // FORCER l'état initial hidden
const isTransitioning = ref(false);
const containerRef = ref(null);
const hasAnimatedInitialEntry = ref(false);

// États affichés (figés pendant les transitions)
const displayedPluginType = ref(props.pluginType);
const displayedPluginState = ref(props.pluginState);
const displayedDeviceName = ref(props.deviceName);

// === FONCTION UTILITAIRE REGROUPÉE ===
function cleanHostname(hostname) {
    if (!hostname) return '';
    return hostname
        .replace('.local', '')
        .replace(/-/g, ' ');
}

// === COMPUTED BASÉS SUR LES ÉTATS AFFICHÉS ===

const displayedIconName = computed(() => {
    return displayedPluginType.value === 'librespot' ? 'spotify' : displayedPluginType.value;
});

const displayedStatusLines = computed(() => {
    // État de démarrage
    if (displayedPluginState.value === 'starting') {
        switch (displayedPluginType.value) {
            case 'bluetooth':
                return ['Démarrage du', 'Bluetooth'];
            case 'roc':
                return ['Démarrage de', 'MacOS'];
            case 'librespot':
                return ['Démarrage de', 'Spotify'];
            default:
                return ['Démarrage...'];
        }
    }

    // État ready : messages d'attente
    if (displayedPluginState.value === 'ready') {
        switch (displayedPluginType.value) {
            case 'bluetooth':
                return ['Bluetooth', 'Prêt à diffuser'];
            case 'roc':
                return ['MacOS', 'Prêt à diffuser'];
            case 'librespot':
                return ['Spotify', 'Prêt à diffuser'];
            default:
                return ['En attente de connexion'];
        }
    }

    // État connected : messages avec nom d'appareil
    if (displayedPluginState.value === 'connected' && displayedDeviceName.value) {
        const cleanedDeviceName = cleanHostname(displayedDeviceName.value);
        
        switch (displayedPluginType.value) {
            case 'bluetooth':
                return ['Connecté à', displayedDeviceName.value]; // Bluetooth garde le nom original
            case 'roc':
                return ['Connecté au', cleanedDeviceName]; // ROC utilise le nom nettoyé
            default:
                return ['Connecté à', displayedDeviceName.value];
        }
    }

    return ['État inconnu'];
});

const displayedShowDisconnectButton = computed(() => {
    if (displayedPluginState.value === 'starting') {
        return false;
    }
    return displayedPluginType.value === 'bluetooth' && displayedPluginState.value === 'connected';
});

// Classes pour les lignes de statut
function getDisplayedStatusLine1Class() {
    if (displayedPluginState.value === 'starting') {
        return 'starting-state';
    }
    if (displayedPluginState.value === 'connected') {
        return 'connected-state';
    }
    return '';
}

function getDisplayedStatusLine2Class() {
    if (displayedPluginState.value === 'starting') {
        return 'starting-state';
    }
    if (displayedPluginState.value === 'connected') {
        return 'connected-state';
    }
    return 'secondary-state';
}

// === GESTION DES ANIMATIONS AVEC TIMING ===

async function updateDisplayedContent() {
    displayedPluginType.value = props.pluginType;
    displayedPluginState.value = props.pluginState;
    displayedDeviceName.value = props.deviceName;
}

async function animateContentChange() {
    if (isTransitioning.value) {
        console.log('⏳ Already transitioning, waiting...');
        while (isTransitioning.value) {
            await new Promise(resolve => setTimeout(resolve, 50));
        }
        console.log('🔄 Retrying animation after wait');
    }
    
    isTransitioning.value = true;
    console.log('🎬 Starting content change animation');
    
    // 1. Animation de sortie vers le HAUT (300ms)
    animationState.value = 'exiting';
    await new Promise(resolve => setTimeout(resolve, 300));
    
    // 2. Changer le contenu pendant qu'invisible
    await updateDisplayedContent();
    await nextTick();
    
    // 3. RESET COMPLET : retour à hidden d'abord
    console.log('🔄 Reset to hidden state');
    animationState.value = 'hidden';
    await new Promise(resolve => requestAnimationFrame(resolve));
    
    // 4. Puis preparing-entry pour forcer la position du bas
    console.log('📥 Move to preparing-entry (BOTTOM)');
    animationState.value = 'preparing-entry';
    await new Promise(resolve => requestAnimationFrame(resolve));
    
    // 5. Animation d'entrée depuis le BAS vers le centre
    console.log('✨ Entry animation should come from BOTTOM');
    animationState.value = 'visible';
    
    setTimeout(() => {
        isTransitioning.value = false;
        console.log('✅ Content change animation complete');
    }, 700);
}

// Animation d'entrée initiale
async function animateInitialEntry() {
    if (hasAnimatedInitialEntry.value) return;
    
    console.log('🎬 Starting PluginStatus initial entry animation');
    hasAnimatedInitialEntry.value = true;
    
    // 1. Mettre à jour le contenu
    await updateDisplayedContent();
    await nextTick();
    
    // 2. Positionner en bas (preparing-entry)
    console.log('📥 Initial entry: preparing-entry (BOTTOM)');
    animationState.value = 'preparing-entry';
    await new Promise(resolve => requestAnimationFrame(resolve));
    
    // 3. Animation d'entrée depuis le BAS vers le centre
    console.log('✨ Initial entry animation from BOTTOM');
    animationState.value = 'visible';
    
    console.log('✅ Initial entry animation complete');
}

// === WATCHERS ===

// Détecter les changements nécessitant une animation
watch(() => [props.pluginType, props.pluginState, props.deviceName], 
    ([newType, newState, newDeviceName], [oldType, oldState, oldDeviceName]) => {
        
        console.log(`🔍 Change detected: ${oldType}/${oldState} → ${newType}/${newState}`);
        
        // Si on n'a pas encore fait l'animation d'entrée initiale, ignorer les changements
        if (!hasAnimatedInitialEntry.value) {
            console.log('📝 Updating content without animation (waiting for initial entry)');
            updateDisplayedContent();
            return;
        }
        
        // Changement de type de plugin : toujours animer
        if (newType !== oldType) {
            console.log('🔄 Plugin type changed, animating');
            animateContentChange();
            return;
        }
        
        // Changement d'état : toujours animer (même starting → ready)
        if (newState !== oldState) {
            console.log('🔄 Plugin state changed, animating');
            animateContentChange();
            return;
        }
        
        // Changement de deviceName : animer seulement si on passe de vide à rempli ou vice versa
        const hadDeviceName = !!oldDeviceName;
        const hasDeviceName = !!newDeviceName;
        
        if (hadDeviceName !== hasDeviceName) {
            console.log('🔄 Device name presence changed, animating');
            animateContentChange();
            return;
        }
        
        // Sinon, mise à jour immédiate sans animation
        console.log('📝 Direct update without animation');
        updateDisplayedContent();
    }, 
    { immediate: false }
);

// CORRIGÉ : Watcher pour shouldAnimate
watch(() => props.shouldAnimate, (shouldAnim) => {
    console.log('🔌 PluginStatus shouldAnimate changed:', shouldAnim, {
        currentState: animationState.value,
        hasAnimated: hasAnimatedInitialEntry.value
    });
    
    if (shouldAnim && !hasAnimatedInitialEntry.value) {
        console.log('✅ PluginStatus - Starting initial entry animation');
        animateInitialEntry();
    } else if (!shouldAnim && hasAnimatedInitialEntry.value) {
        console.log('❌ PluginStatus - Resetting for exit');
        animationState.value = 'hidden';
        hasAnimatedInitialEntry.value = false;
    }
}, { immediate: true });

// Initialisation du contenu au montage (sans animation)
watch(() => props.pluginState, (newState) => {
    if (newState && !hasAnimatedInitialEntry.value) {
        console.log('📝 Initial content setup without animation');
        updateDisplayedContent();
    }
}, { immediate: true });

// === GESTIONNAIRE D'ÉVÉNEMENTS ===

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
    transition: all var(--transition-spring);
}

/* État caché par défaut */
.plugin-status.state-hidden {
    opacity: 0;
    transform: translateY(var(--space-06)) scale(0.95);
}

/* État visible normal */
.plugin-status.state-visible {
    opacity: 1;
    transform: translateY(0) scale(1);
}

/* État de sortie - VA VERS LE HAUT */
.plugin-status.state-exiting {
    opacity: 0;
    transform: translateY(calc(-1 * var(--space-06))) scale(0.95);
}

/* État de préparation d'entrée - BAS SANS TRANSITION */
.plugin-status.state-preparing-entry {
    opacity: 0;
    transform: translateY(calc(var(--space-06) + 4px)) scale(0.95);
    transition: none !important; /* PAS de transition pour le placement */
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

/* États par défaut */
.status-single h2,
.status-line-1 h2,
.status-line-2 h2 {
    color: var(--color-text);
}

/* États spéciaux ligne 1 */
.status-line-1.starting-state h2,
.status-line-1.connected-state h2 {
    color: var(--color-text-secondary);
}

/* États spéciaux ligne 2 */
.status-line-2.starting-state h2,
.status-line-2.connected-state h2 {
    color: var(--color-text);
}

.status-line-2.secondary-state h2 {
    color: var(--color-text-secondary);
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