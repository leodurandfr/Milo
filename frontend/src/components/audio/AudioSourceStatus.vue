<!-- AudioSourceStatus.vue -->
<template>
  <div class="source-status">
    <div class="source-status-content">
      <div class="source-status-inner">
        <!-- Device info section -->
        <div class="device-info">
          <div class="device-info-content">
            <div class="device-info-inner">
              <!-- Source icon -->
              <div class="source-icon">
                <LoadingSpinner v-if="sourceState === 'starting' || sourceState === 'loading_disc' || sourceState === 'ejecting'" :size="26" variant="background" />
                <AppIcon v-else :name="sourceType" :size="32" />
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

        <!-- Bottom action button: Bluetooth disconnect or Qobuz connect-account CTA.
             The <button> IS the full-width bar so the whole surface is clickable. -->
        <button v-if="actionButton" @click="actionButton.onClick" :disabled="actionButton.disabled"
          class="action-button heading-3">{{ actionButton.label }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import AppIcon from '@/components/ui/AppIcon.vue';
import { ALL_AUDIO_SOURCES } from '@/constants/audioSources';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import { useI18n } from '@/services/i18n';
import { formatDeviceNames } from '@/utils/deviceName';

const { t } = useI18n();

// Props
const props = defineProps({
  sourceType: {
    type: String,
    required: true,
    validator: (value) => value === 'none' || ALL_AUDIO_SOURCES.includes(value)
  },
  sourceState: {
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
  },
  // False only when the source needs an account and none is connected (Qobuz):
  // swaps the idle line to "account not connected" and arms the connect CTA.
  // Default true so sources without a login requirement are unaffected.
  accountConnected: {
    type: Boolean,
    default: true
  }
});

// Emits
const emit = defineEmits(['disconnect', 'connect']);

// === COMPUTED FOR DISPLAYED CONTENT ===
const displayedStatusLines = computed(() => {
  // CD: ejecting disc
  if (props.sourceState === 'ejecting') {
    return [t('audioSources.cd'), t('status.ejecting')];
  }
  // CD: disc inserted, loading album (spinner shown via loading_disc state)
  if (props.sourceState === 'loading_disc') {
    return [t('audioSources.cd'), t('status.loadingAlbum')];
  }
  // CD: no drive connected
  if (props.sourceState === 'no_drive') {
    return [t('audioSources.cd'), t('audioSources.cdSource.noDriveConnected')];
  }

  // Starting state
  if (props.sourceState === 'starting') {
    switch (props.sourceType) {
      case 'bluetooth':
        return [t('status.loadingOfMasculine'), t('audioSources.bluetooth')];
      case 'mac':
        return [t('status.loadingOfMasculine'), t('audioSources.macOS')];
      case 'spotify':
        return [t('status.loadingOf'), t('audioSources.spotify')];
      case 'radio':
        return [t('status.loadingOfFeminine'), t('audioSources.radio')];
      case 'podcast':
        return [t('status.loadingOf'), t('audioSources.podcasts')];
      case 'airplay':
        return [t('status.loadingOf'), t('audioSources.airplay')];
      case 'dlna':
        return [t('status.loadingOf'), t('audioSources.dlna')];
      case 'qobuz':
        return [t('status.loadingOf'), t('audioSources.qobuz')];
      case 'cd':
        return [t('status.loadingOfMasculine'), t('audioSources.cd')];
      default:
        return [t('status.loading')];
    }
  }

  // Ready state: waiting messages
  if (props.sourceState === 'waiting') {
    switch (props.sourceType) {
      case 'bluetooth':
        return [t('audioSources.bluetooth'), t('status.ready')];
      case 'mac':
        return [t('audioSources.macOS'), t('status.readyToStream')];
      case 'spotify':
        return [t('audioSources.spotify'), t('status.ready')];
      case 'radio':
        return [t('audioSources.radio'), t('status.readyToStream')];
      case 'podcast':
        return [t('audioSources.podcasts'), t('status.ready')];
      case 'airplay':
        return [t('audioSources.airplay'), t('status.readyToStream')];
      case 'dlna':
        return [t('audioSources.dlna'), t('status.readyToStream')];
      case 'qobuz':
        // No Qobuz account logged in → point the user at the account login
        // rather than the (unreachable) "ready to stream" state.
        return props.accountConnected
          ? [t('audioSources.qobuz'), t('status.readyToStream')]
          : [t('audioSources.qobuz'), t('status.accountNotConnected')];
      case 'cd':
        return [t('audioSources.cd'), t('status.readyToPlay')];
      default:
        return [t('status.ready')];
    }
  }

  // Connected state: messages with device name
  if (props.sourceState === 'active' && props.deviceName) {
    const formattedDeviceNames = formatDeviceNames(props.deviceName);

    switch (props.sourceType) {
      case 'bluetooth':
        return [t('status.connectedTo'), formattedDeviceNames];
      case 'mac':
        return [t('status.audioReceivedFrom'), formattedDeviceNames];
      case 'airplay':
        return [t('status.connectedTo'), formattedDeviceNames];
      default:
        return [t('status.connectedTo'), formattedDeviceNames];
    }
  }

  // DLNA active without a controller identity: UPnP exposes no "who's casting"
  // name, and a controller may push only a bare title (no artist/cover) so the
  // rich player is gated out — show a playing state, not the waiting fallback.
  if (props.sourceState === 'active' && props.sourceType === 'dlna') {
    return [t('audioSources.dlna'), t('status.playing')];
  }

  // Qobuz active in the brief window before the first now_playing arrives (no
  // title/artist yet → rich player gated out). The proxy exposes no controller
  // identity, so show a playing state rather than the waiting fallback.
  if (props.sourceState === 'active' && props.sourceType === 'qobuz') {
    return [t('audioSources.qobuz'), t('status.playing')];
  }

  return [t('status.waiting')];
});

// Single bottom action button on the card, or null to hide it. The two cases are
// mutually exclusive: Bluetooth's disconnect (active) and Qobuz's connect-account
// CTA (waiting with no account). 'starting' never shows a button.
const actionButton = computed(() => {
  if (props.sourceType === 'bluetooth' && props.sourceState === 'active') {
    return {
      label: props.isDisconnecting ? t('status.disconnecting') : t('status.disconnect'),
      disabled: props.isDisconnecting,
      onClick: () => emit('disconnect'),
    };
  }
  if (props.sourceType === 'qobuz' && props.sourceState === 'waiting' && !props.accountConnected) {
    return {
      label: t('status.connect'),
      disabled: false,
      onClick: () => emit('connect'),
    };
  }
  return null;
});

// Classes for status lines
function getDisplayedStatusLine1Class() {
  if (props.sourceState === 'starting') {
    return 'starting-state';
  }
  if (props.sourceState === 'active') {
    return 'active-state';
  }
  return '';
}

function getDisplayedStatusLine2Class() {
  if (props.sourceState === 'starting') {
    return 'starting-state';
  }
  if (props.sourceState === 'active') {
    return 'active-state';
  }
  return 'secondary-state';
}
</script>

<style scoped>
/* === COMPONENT STYLES === */
.source-status {
  background: var(--color-background-neutral);
  border-radius: var(--radius-07);
  box-shadow: var(--shadow-02);
  width: 364px;
  position: relative;
  margin: auto;
}

.source-status-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  position: relative;
  height: 100%;
}

.source-status-inner {
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

/* Source icon */
.source-icon {
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
.status-line-1.active-state h2 {
  color: var(--color-text-secondary);
}

/* Special states line 2 */
.status-line-2.starting-state h2,
.status-line-2.active-state h2 {
  color: var(--color-text);
}

.status-line-2.secondary-state h2 {
  color: var(--color-text-secondary);
}

/* Bottom action button (disconnect / connect) — the <button> IS the full-width
   bar so the entire surface is clickable, not just the label. */
.action-button {
  box-sizing: border-box;
  width: 100%;
  height: 42px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-02) var(--space-05);
  background: var(--color-background-strong);
  border: none;
  border-radius: var(--radius-04);
  color: var(--color-text-secondary);
  white-space: nowrap;
  cursor: pointer;
}

.action-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .source-status {
    width: 100%;
    max-width: 348px;
  }
}
</style>