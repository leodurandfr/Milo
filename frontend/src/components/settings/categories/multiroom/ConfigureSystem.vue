<!-- frontend/src/components/settings/categories/multiroom/ConfigureSystem.vue -->
<!-- Form for configuring a discovered system (ethernet pending or wifi hotspot adoption). -->
<template>
  <SectionStack>
    <!-- Rebooting State -->
    <MessageContent
      v-if="isRebooting"
      icon="multiroom"
      :loading="!rebootTimedOut"
      :loading-delay="0"
      :title="rebootingTitle"
      :subtitle="rebootTimedOut ? t('multiroom.pending.rebootTimeout') : rebootingSubtitle"
    />

    <!-- Error State: the sentence says what failed, the server's words folded under it. -->
    <template v-else-if="error">
      <MessageContent
        icon="multiroom"
        :title="t('multiroom.pending.errorTitle')"
        :cta-label="t('multiroom.pending.retry')"
        :cta-click="resetError"
      />
      <NoticeBox :detail="errorDetail">{{ error }}</NoticeBox>
    </template>

    <!-- Configuration Form -->
    <template v-else>
      <!-- Speaker Name -->
      <SectionCard :title="t('multiroom.systemNameRemote')">
        <InputText
          v-model="speakerName"
          :placeholder="t('multiroom.pending.namePlaceholder')"
          :maxlength="16"
        />
      </SectionCard>

      <!-- WiFi connection (wifi mode only) -->
      <SectionCard v-if="mode === 'wifi'" :title="t('multiroom.adopt.networkSection')">
        <!-- The server's own network: its key never leaves the server -->
        <template v-if="canUseServerWifi && useServerWifi">
          <div class="server-wifi-row">
            <SvgIcon name="wifi" :size="24" />
            <span class="text-body-medium server-wifi-row__ssid">
              {{ t('multiroom.adopt.useServerWifi', { ssid: discoveryStore.serverWifi.ssid }) }}
            </span>
            <Button variant="control" size="small" @click="useServerWifi = false">
              {{ t('multiroom.adopt.changeNetwork') }}
            </Button>
          </div>
        </template>

        <!-- Manual entry via NetworkSelector -->
        <template v-else>
          <p v-if="!canUseServerWifi" class="text-body-medium adopt-hint">
            {{ t('multiroom.adopt.enterCredentials') }}
          </p>
          <NetworkSelector
            :show-country="false"
            :show-label="false"
            :submit-action="null"
            :show-connect-error="false"
            @update:wifi="onManualWifiUpdate"
          />
        </template>

        <p class="text-body-small adopt-hint">{{ t('multiroom.adopt.ethernetHint') }}</p>
      </SectionCard>

      <!-- Audio Card Selection -->
      <SectionCard :title="t('multiroom.pending.audioCard')">
        <div class="audio-list">
          <ListItemButton
            v-for="card in audioCards"
            :key="card.value"
            :title="card.label"
            action="radio"
            :model-value="selectedAudioId === card.value"
            @click="selectedAudioId = card.value; confirmReboot = false"
          />
        </div>
      </SectionCard>

      <!-- Speaker Type Selection -->
      <SectionCard :title="t('multiroom.systemType')">
        <div class="speaker-types">
          <ListItemButton
            v-for="type in speakerTypes"
            :key="type.value"
            :title="type.label"
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
      </SectionCard>

      <!-- Speaker Info (ethernet only — IP from pending registry) -->
      <SectionCard v-if="mode === 'ethernet'" :title="t('multiroom.systemInfo')">
        <div class="info-item">
          <span class="info-label text-mono-medium">{{ t('clientDetails.ipAddress') }}</span>
          <span class="info-value text-mono-medium">{{ pendingClient?.ip || t('common.unknown') }}</span>
        </div>
      </SectionCard>

      <!-- Apply Button (two-step confirm like HardwareSettings) -->
      <Button
        :variant="confirmReboot ? 'important' : 'brand'"
        size="medium"
        class="apply-button-sticky" floating
        :disabled="!canApply"
        @click="handleApply"
      >
        {{ applyButtonLabel }}
      </Button>
    </template>
  </SectionStack>
</template>

<script setup>
import { ref, computed, onMounted, watch, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useDiscoveryStore } from '@/stores/discoveryStore';
import { useHardwareConfig } from '@/composables/useHardwareConfig';
import { useTimer } from '@/composables/useTimer';
import { adoptionErrorKey } from '@/utils/adoptionError';
import InputText from '@/components/ui/InputText.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import Button from '@/components/ui/Button.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import NoticeBox from '@/components/ui/NoticeBox.vue';
import SectionStack from '@/components/ui/SectionStack.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
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
const timer = useTimer();
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
// The server's detail for the failure `error` says, folded under it.
const errorDetail = ref(null);
const audioCards = ref([]);
let rebootTimeoutId = null;
const REBOOT_TIMEOUT_MS = 120000; // 2 minutes

// Reactive reference to the pending client (ethernet mode only)
const pendingClient = computed(() => multiroomStore.pendingClients.get(props.macId));

const canUseServerWifi = computed(() => discoveryStore.serverWifi?.available === true);

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
  errorDetail.value = null;
  if (audioCards.value.length === 0) {
    await loadAudioCards();
  }
  // The refusal may be that the server moved to another network since.
  if (props.mode === 'wifi') {
    await discoveryStore.loadServerWifi();
    if (!canUseServerWifi.value) useServerWifi.value = false;
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
  errorDetail.value = null;

  try {
    if (props.mode === 'wifi') {
      const typedWifi = useServerWifi.value && canUseServerWifi.value
        ? null
        : { ssid: manualWifi.value.ssid, password: manualWifi.value.password || '' };

      // Mirror the ethernet watchdog: if the request stalls (server stuck on
      // hotspot, restore wifi failure, etc.) flip the UI to a timeout state
      // instead of spinning indefinitely.
      rebootTimeoutId = timer.setTimeout(() => {
        rebootTimedOut.value = true;
      }, REBOOT_TIMEOUT_MS);
      await discoveryStore.adoptSpeaker({
        ssid: props.hotspotSsid,
        audio_id: selectedAudioId.value,
        // The adoption needs a name (the satellite is told it before it
        // ever reaches the LAN), so an empty field sends the word for one.
        speaker_name: speakerName.value.trim() || t('multiroom.defaultSpeakerName'),
        speaker_type: selectedSpeakerType.value,
      }, typedWifi, { errorRef: errorDetail });
      // Server returns once the device has accepted the config and is rebooting.
      // The new client appears in the multiroom list when it joins the LAN.
      timer.clear(rebootTimeoutId);
      rebootTimeoutId = null;
      emit('back');
    } else {
      await multiroomStore.configurePendingClient(props.macId, {
        name: speakerName.value.trim() || null,
        speaker_type: selectedSpeakerType.value,
        audio_id: selectedAudioId.value,
      }, { errorRef: errorDetail });
      rebootTimeoutId = timer.setTimeout(() => {
        rebootTimedOut.value = true;
      }, REBOOT_TIMEOUT_MS);
    }
  } catch (e) {
    if (rebootTimeoutId) {
      timer.clear(rebootTimeoutId);
      rebootTimeoutId = null;
    }
    isRebooting.value = false;
    error.value = props.mode === 'wifi'
      ? t(adoptionErrorKey(e?.response?.data?.detail))
      : t('multiroom.pending.errorGeneric');
  }
}

onMounted(async () => {
  // Load audio card options from hardware registry
  const loaded = await loadAudioCards();
  if (!loaded) return;

  if (props.mode === 'wifi') {
    // Usually preloaded by MultiroomSettings, but we may navigate here
    // without that guarantee on a refresh.
    if (discoveryStore.serverWifi === null) {
      await discoveryStore.loadServerWifi();
    }
    // Default to manual mode if the server isn't on wifi.
    if (!canUseServerWifi.value) {
      useServerWifi.value = false;
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
  // rebootTimeoutId (if still pending) is auto-cleared by useTimer.
});
</script>

<style scoped>
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
  background: var(--color-inset);
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
  background: var(--color-inset);
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
@media (max-aspect-ratio: 4/3) and (max-width: 1023px) {
  .audio-list,
  .speaker-types {
    grid-template-columns: 1fr;
  }
}
</style>
