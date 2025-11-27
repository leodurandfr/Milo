<!-- AudioSourceStatus.vue -->
<template>
  <div class="plugin-status">
    <div class="plugin-status-content">
      <div class="plugin-status-inner">
        <!-- Device info section -->
        <div class="device-info">
          <div class="device-info-content">
            <div class="device-info-inner">
              <!-- Plugin icon -->
              <div class="plugin-icon">
                <LoadingSpinner v-if="pluginState === 'starting'" :size="26" variant="background" />
                <AppIcon v-else :name="pluginType" :size="32" />
              </div>

              <!-- Text status -->
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

        <!-- Disconnect button (conditional) -->
        <div v-if="displayedShowDisconnectButton" class="disconnect-button">
          <div class="disconnect-button-content">
            <div class="disconnect-button-inner">
              <button @click="handleDisconnect" :disabled="isDisconnecting" class="disconnect-text">
                <p>{{ isDisconnecting ? $t('status.disconnecting') : $t('status.disconnect') }}</p>
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
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import { useI18n } from '@/services/i18n';

const { t } = useI18n();

// Props
const props = defineProps({
  pluginType: {
    type: String,
    required: true,
    validator: (value) => ['librespot', 'bluetooth', 'roc', 'radio', 'podcast'].includes(value)
  },
  pluginState: {
    type: String,
    required: true
  },
  deviceName: {
    type: [String, Array],  // Support string or array for ROC multi-clients
    default: ''
  },
  isDisconnecting: {
    type: Boolean,
    default: false
  }
});

// Emits
const emit = defineEmits(['disconnect']);

// === UTILITY FUNCTIONS ===
function cleanDeviceName(deviceName) {
  if (!deviceName) return '';
  return deviceName
    .replace('.local', '')
    .replace(/-/g, ' ');
}

// Format deviceName which can be string or array
function formatDeviceNames(deviceName) {
  if (!deviceName) return '';

  // If it's an array (ROC multi-clients)
  if (Array.isArray(deviceName)) {
    if (deviceName.length === 0) return '';
    // Join with \n for line breaks (requires white-space: pre-line in CSS)
    return deviceName.map(name => cleanDeviceName(name)).join('\n');
  }

  // If it's a string (normal case)
  return cleanDeviceName(deviceName);
}

// === COMPUTED FOR DISPLAYED CONTENT ===
const displayedStatusLines = computed(() => {
  // Starting state
  if (props.pluginState === 'starting') {
    switch (props.pluginType) {
      case 'bluetooth':
        return [t('status.loadingOfMasculine'), t('audioSources.bluetooth')];
      case 'roc':
        return [t('status.loadingOfMasculine'), t('audioSources.macReceiver')];
      case 'librespot':
        return [t('status.loadingOf'), t('audioSources.spotify')];
      case 'radio':
        return [t('status.loadingOfFeminine'), t('audioSources.radio')];
      case 'podcast':
        return [t('status.loadingOf'), 'Podcasts'];
      default:
        return [t('status.loading')];
    }
  }

  // Ready state: waiting messages
  if (props.pluginState === 'ready') {
    switch (props.pluginType) {
      case 'bluetooth':
        return [t('audioSources.bluetooth'), t('status.ready')];
      case 'roc':
        return [t('audioSources.macReceiver'), t('status.readyToStream')];
      case 'librespot':
        return [t('audioSources.spotify'), t('status.ready')];
      case 'radio':
        return [t('audioSources.radio'), t('status.readyToStream')];
      case 'podcast':
        return ['Podcasts', t('status.ready')];
      default:
        return [t('status.ready')];
    }
  }

  // Connected state: messages with device name
  if (props.pluginState === 'connected' && props.deviceName) {
    const formattedDeviceNames = formatDeviceNames(props.deviceName);

    switch (props.pluginType) {
      case 'bluetooth':
        return [t('status.connectedTo'), formattedDeviceNames];
      case 'roc':
        return [t('status.connectedToMac'), formattedDeviceNames];
      default:
        return [t('status.connectedTo'), formattedDeviceNames];
    }
  }

  return [t('status.waiting')];
});

const displayedShowDisconnectButton = computed(() => {
  if (props.pluginState === 'starting') {
    return false;
  }
  return props.pluginType === 'bluetooth' && props.pluginState === 'connected';
});

// Classes for status lines
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

// === EVENT HANDLER ===
function handleDisconnect() {
  emit('disconnect');
}
</script>

<style scoped>
/* === COMPONENT STYLES === */
.plugin-status {
  background: var(--color-background-neutral);
  border-radius: var(--radius-07);
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

/* Plugin icon */
.plugin-icon {
  position: relative;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  background: var(--color-background);
  border-radius: var(--radius-02);
}

/* Text status */
.device-status {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}

/* Default states */
.status-single h2,
.status-line-1 h2,
.status-line-2 h2 {
  color: var(--color-text);
}

/* Line-break support for ROC multi-clients */
.status-line-2 h2 {
  white-space: pre-line;
}

/* Special states line 1 */
.status-line-1.starting-state h2,
.status-line-1.connected-state h2 {
  color: var(--color-text-secondary);
}

/* Special states line 2 */
.status-line-2.starting-state h2,
.status-line-2.connected-state h2 {
  color: var(--color-text);
}

.status-line-2.secondary-state h2 {
  color: var(--color-text-secondary);
}

/* Disconnect button */
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