<!-- frontend/src/components/settings/categories/multiroom/ConfigureSystem.vue -->
<!-- Form for configuring a discovered system (ethernet pending or wifi hotspot adoption). -->
<template>
  <div class="configure-speaker">
    <!-- Rebooting State -->
    <MessageContent
      v-if="isRebooting"
      icon="multiroom"
      :loading="!rebootTimedOut"
      :loading-delay="0"
      :title="rebootingTitle"
      :subtitle="rebootTimedOut ? t('multiroom.pending.rebootTimeout') : rebootingSubtitle"
    />

    <!-- Error State -->
    <MessageContent
      v-else-if="error"
      icon="multiroom"
      :title="t('multiroom.pending.errorTitle')"
      :subtitle="error"
      :cta-label="t('multiroom.pending.retry')"
      :cta-click="resetError"
    />

    <!-- Configuration Form -->
    <template v-else>
      <!-- Network connection (wifi mode only) -->
      <SettingsSection v-if="mode === 'wifi'" :title="t('multiroom.adopt.networkSection')">
        <!-- Auto-fill from server's active wifi -->
        <template v-if="canUseServerWifi && useServerWifi">
          <div class="server-wifi-row">
            <SvgIcon name="wifi" :size="24" />
            <span class="text-body server-wifi-row__ssid">
              {{ t('multiroom.adopt.useServerWifi', { ssid: discoveryStore.serverWifiCreds.ssid }) }}
            </span>
            <Button variant="background-strong" size="small" @click="useServerWifi = false">
              {{ t('multiroom.adopt.changeNetwork') }}
            </Button>
          </div>
        </template>

        <!-- Manual entry via NetworkSelector -->
        <template v-else>
          <p v-if="!canUseServerWifi" class="text-mono adopt-hint">
            {{ t('multiroom.adopt.enterCredentials') }}
          </p>
          <NetworkSelector
            :show-country="false"
            :show-label="false"
            :submit-action="null"
            @update:wifi="onManualWifiUpdate"
          />
        </template>
      </SettingsSection>

      <!-- Speaker Name -->
      <SettingsSection :title="t('multiroom.systemNameRemote')">
        <InputText
          v-model="speakerName"
          :placeholder="t('multiroom.pending.namePlaceholder')"
          size="medium"
          :maxlength="16"
        />
      </SettingsSection>

      <!-- Audio Card Selection -->
      <SettingsSection :title="t('multiroom.pending.audioCard')">
        <div class="audio-list">
          <ListItemButton
            v-for="card in audioCardOptions"
            :key="card.value"
            :title="card.label"
            variant="background"
            action="radio"
            :model-value="selectedAudioId === card.value"
            @click="selectedAudioId = card.value; confirmReboot = false"
          />
        </div>
      </SettingsSection>

      <!-- Speaker Type Selection -->
      <SettingsSection :title="t('multiroom.systemType')">
        <div class="speaker-types">
          <ListItemButton
            v-for="type in speakerTypes"
            :key="type.value"
            :title="type.label"
            variant="background"
            action="radio"
            icon-variant="standard"
            :model-value="selectedSpeakerType === type.value"
            @click="selectedSpeakerType = type.value"
          >
            <template #icon>
              <SvgIcon :name="type.icon" :size="28" />
            </template>
          </ListItemButton>
        </div>
      </SettingsSection>

      <!-- Speaker Info (ethernet only — IP from pending registry) -->
      <SettingsSection v-if="mode === 'ethernet'" :title="t('multiroom.systemInfo')">
        <div class="info-item">
          <span class="info-label text-mono">{{ t('clientDetails.ipAddress') }}</span>
          <span class="info-value text-mono">{{ pendingClient?.ip || 'Unknown' }}</span>
        </div>
      </SettingsSection>

      <!-- Apply Button (two-step confirm like HardwareSettings) -->
      <Button
        :variant="confirmReboot ? 'important' : 'brand'"
        size="medium"
        class="apply-button-sticky"
        :disabled="!canApply"
        @click="handleApply"
      >
        {{ applyButtonLabel }}
      </Button>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useDiscoveryStore } from '@/stores/discoveryStore';
import { useHardwareConfig } from '@/composables/useHardwareConfig';
import InputText from '@/components/ui/InputText.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import Button from '@/components/ui/Button.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import NetworkSelector from '@/components/network/NetworkSelector.vue';

const props = defineProps({
  macId: {
    type: String,
    default: null,
  },
  mode: {
    type: String,
    default: 'ethernet',
    validator: (v) => v === 'ethernet' || v === 'wifi',
  },
  hotspotSsid: {
    type: String,
    default: null,
  },
});

const emit = defineEmits(['back']);

const { t } = useI18n();
const multiroomStore = useMultiroomStore();
const discoveryStore = useDiscoveryStore();
const { loadHardwareConfig } = useHardwareConfig();

// Form state
const speakerName = ref('');
const selectedAudioId = ref('');
const selectedSpeakerType = ref('bookshelf');

// Wifi mode state
const useServerWifi = ref(true);
const manualWifi = ref(null); // { ssid, password, security } from NetworkSelector

// UI state
const isRebooting = ref(false);
const rebootTimedOut = ref(false);
const confirmReboot = ref(false);
const error = ref('');
const audioCards = ref([]);
let rebootTimeoutId = null;
const REBOOT_TIMEOUT_MS = 120000; // 2 minutes

// Reactive reference to the pending client (ethernet mode only)
const pendingClient = computed(() => multiroomStore.pendingClients.get(props.macId));

// 4-char MAC suffix derived from "Milō-XXXX".
const macSuffix = computed(() => {
  if (!props.hotspotSsid) return '';
  const parts = props.hotspotSsid.split('-');
  return parts.length > 1 ? parts[parts.length - 1] : '';
});

const canUseServerWifi = computed(() =>
  discoveryStore.serverWifiCreds?.available === true
  && !!discoveryStore.serverWifiCreds?.ssid
);

// Filter out "none" — the whole point of this form is to configure audio
const audioCardOptions = computed(() =>
  audioCards.value.filter(card => card.value !== 'none')
);

// Speaker type options (same as ClientEdit)
const speakerTypes = computed(() => [
  { value: 'satellite', label: t('multiroom.systemTypes.satellite'), icon: 'speakerSatellite' },
  { value: 'bookshelf', label: t('multiroom.systemTypes.bookshelf'), icon: 'speakerShelf' },
  { value: 'tower', label: t('multiroom.systemTypes.tower'), icon: 'speakerColumn' },
  { value: 'subwoofer', label: t('multiroom.systemTypes.subwoofer'), icon: 'speakerSub' },
]);

const wifiCredsReady = computed(() => {
  if (props.mode !== 'wifi') return true;
  if (canUseServerWifi.value && useServerWifi.value) return true;
  return !!manualWifi.value?.ssid
    && (manualWifi.value.security === '' || !!manualWifi.value.password);
});

const canApply = computed(() => !!selectedAudioId.value && wifiCredsReady.value);

const rebootingTitle = computed(() =>
  props.mode === 'wifi'
    ? t('multiroom.adopt.adopting')
    : t('multiroom.pending.rebootingMessage')
);

const rebootingSubtitle = computed(() =>
  props.mode === 'wifi'
    ? null
    : t('multiroom.pending.rebootingDescription')
);

// Watch for the pending client being removed (ethernet adoption: client moved
// to the real registry once it reconnects). Wifi adoption returns synchronously
// from adoptSpeaker, so we don't need this watcher there.
const stopWatch = watch(
  () => multiroomStore.pendingClients.get(props.macId),
  (newVal) => {
    if (props.mode !== 'ethernet') return;
    if (isRebooting.value && !newVal) {
      emit('back');
    }
  },
);

function onManualWifiUpdate(creds) {
  manualWifi.value = creds;
  confirmReboot.value = false;
}

async function loadAudioCards() {
  const config = await loadHardwareConfig(true);
  if (config?.options?.audio_cards) {
    audioCards.value = config.options.audio_cards;
    return true;
  }
  error.value = t('multiroom.pending.errorLoadingHardware');
  return false;
}

async function resetError() {
  error.value = '';
  if (audioCards.value.length === 0) {
    await loadAudioCards();
  }
}

const applyButtonLabel = computed(() => {
  if (confirmReboot.value) return t('multiroom.pending.confirmReboot');
  return t('multiroom.pending.applyAndReboot');
});

function handleApply() {
  if (!confirmReboot.value) {
    confirmReboot.value = true;
    return;
  }
  applyConfiguration();
}

async function applyConfiguration() {
  if (!canApply.value) return;

  isRebooting.value = true;
  error.value = '';

  try {
    if (props.mode === 'wifi') {
      const wifiCreds = useServerWifi.value && canUseServerWifi.value
        ? {
            ssid: discoveryStore.serverWifiCreds.ssid,
            password: discoveryStore.serverWifiCreds.password || '',
          }
        : {
            ssid: manualWifi.value.ssid,
            password: manualWifi.value.password || '',
          };

      await discoveryStore.adoptSpeaker({
        ssid: props.hotspotSsid,
        audio_id: selectedAudioId.value,
        speaker_name: speakerName.value.trim() || `Speaker-${macSuffix.value}`,
        speaker_type: selectedSpeakerType.value,
        wifi_ssid: wifiCreds.ssid,
        wifi_password: wifiCreds.password,
      });
      // Server returns once the device has accepted the config and is rebooting.
      // The new client appears in the multiroom list when it joins the LAN.
      emit('back');
    } else {
      await multiroomStore.configurePendingClient(props.macId, {
        name: speakerName.value.trim() || null,
        speaker_type: selectedSpeakerType.value,
        audio_id: selectedAudioId.value,
      });
      rebootTimeoutId = setTimeout(() => {
        rebootTimedOut.value = true;
      }, REBOOT_TIMEOUT_MS);
    }
  } catch (e) {
    isRebooting.value = false;
    error.value = e?.response?.data?.detail
      || (props.mode === 'wifi' ? t('multiroom.adopt.errorPush') : t('multiroom.pending.errorGeneric'));
  }
}

onMounted(async () => {
  // Load audio card options from hardware registry
  const loaded = await loadAudioCards();
  if (!loaded) return;

  if (props.mode === 'wifi') {
    // Ensure server wifi creds are loaded — usually preloaded by MultiroomSettings,
    // but we may navigate here without that guarantee on a refresh.
    if (discoveryStore.serverWifiCreds === null) {
      await discoveryStore.loadServerWifiCreds();
    }
    // Default to manual mode if the server isn't on wifi.
    if (!canUseServerWifi.value) {
      useServerWifi.value = false;
    }
    // Pre-fill the speaker name with the MAC-derived default; user can override.
    if (!speakerName.value) {
      speakerName.value = `Speaker-${macSuffix.value}`;
    }
    return;
  }

  // Ethernet mode: pre-fill from pending client data.
  const client = multiroomStore.pendingClients.get(props.macId);
  if (client) {
    speakerName.value = client.name || '';
    selectedSpeakerType.value = client.speaker_type || 'bookshelf';
    if (client.audio_id && client.audio_id !== 'none') {
      selectedAudioId.value = client.audio_id;
    }
  }
});

onUnmounted(() => {
  stopWatch();
  if (rebootTimeoutId) clearTimeout(rebootTimeoutId);
});
</script>

<style scoped>
.configure-speaker {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.audio-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-01);
}

.speaker-types {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-01);
}

.info-item {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: var(--space-03) var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-background-strong);
}

.info-label {
  color: var(--color-text-secondary);
}

.info-value {
  color: var(--color-text);
  text-align: right;
}

.server-wifi-row {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-03) var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-background);
}

.server-wifi-row__ssid {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.adopt-hint {
  color: var(--color-text-secondary);
}

.apply-button-sticky {
  position: sticky;
  bottom: 0;
  width: 100%;
  z-index: 10;
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .audio-list,
  .speaker-types {
    grid-template-columns: 1fr;
  }
}
</style>
