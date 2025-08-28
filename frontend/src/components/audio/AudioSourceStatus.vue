<!-- AudioSourceStatus.vue - Version SIMPLIFIÉE avec traductions -->
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
                <p>{{ isDisconnecting ? $t('Déconnexion...') : $t('Déconnecter') }}</p>
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
import AppIcon from '@/components/ui/AppIcon.vue';
import { useI18n } from '@/services/i18n';

const { t } = useI18n();

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

// === FONCTION UTILITAIRE ===
function cleanDeviceName(deviceName) {
  if (!deviceName) return '';
  return deviceName
    .replace('.local', '')
    .replace(/-/g, ' ');
}

// === COMPUTED POUR LE CONTENU AFFICHÉ ===
const displayedIconName = computed(() => {
  return props.pluginType === 'librespot' ? 'spotify' : props.pluginType;
});

const displayedPluginState = computed(() => {
  return props.pluginState;
});

const displayedStatusLines = computed(() => {
  // État de démarrage
  if (props.pluginState === 'starting') {
    switch (props.pluginType) {
      case 'bluetooth':
        return [t('Chargement du'), t('Bluetooth')];
      case 'roc':
        return [t('Chargement du'), t('Récepteur audio macOS')];
      case 'librespot':
        return [t('Chargement de'), t('Spotify')];
      default:
        return [t('Chargement...')];
    }
  }

  // État ready : messages d'attente
  if (props.pluginState === 'ready') {
    switch (props.pluginType) {
      case 'bluetooth':
        return [t('Bluetooth'), t('Prêt à se connecter')];
      case 'roc':
        return [t('Audio macOS'), t('Prêt à diffuser')];
      case 'librespot':
        return [t('Spotify'), t('Prêt à se connecter')];
      default:
        return [t('Prêt à se connecter')];
    }
  }

  // État connected : messages avec nom d'appareil
  if (props.pluginState === 'connected' && props.deviceName) {
    const cleanedDeviceName = cleanDeviceName(props.deviceName);
    
    switch (props.pluginType) {
      case 'bluetooth':
        return [t('Connecté à'), cleanedDeviceName];
      case 'roc':
        return [t('Connecté au'), cleanedDeviceName];
      default:
        return [t('Connecté à'), props.deviceName];
    }
  }

  return [t('En attente...')];
});

const displayedShowDisconnectButton = computed(() => {
  if (props.pluginState === 'starting') {
    return false;
  }
  return props.pluginType === 'bluetooth' && props.pluginState === 'connected';
});

// Classes pour les lignes de statut
function getDisplayedStatusLine1Class() {
  if (props.pluginState === 'starting') {
    return 'starting-state';
  }
  if (props.pluginState === 'connected') {
    return 'connected-state';
  }
  return '';
}

function getDisplayedStatusLine2Class() {
  if (props.pluginState === 'starting') {
    return 'starting-state';
  }
  if (props.pluginState === 'connected') {
    return 'connected-state';
  }
  return 'secondary-state';
}

// === GESTIONNAIRE D'ÉVÉNEMENTS ===
function handleDisconnect() {
  emit('disconnect');
}
</script>

<style scoped>
/* === STYLES DU COMPOSANT === */
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
  gap: var(--space-03);
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