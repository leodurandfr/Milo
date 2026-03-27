<!-- frontend/src/components/settings/categories/multiroom/ClientEdit.vue -->
<!-- Form for editing a single client's settings -->
<template>
  <div class="client-edit">
    <!-- Rebooting State (after audio card change) -->
    <MessageContent
      v-if="isRebooting"
      icon="multiroom"
      :loading="!rebootTimedOut"
      :loading-delay="0"
      :title="t('multiroom.pending.rebootingMessage')"
      :subtitle="rebootTimedOut ? t('multiroom.pending.rebootTimeout') : t('multiroom.pending.rebootingDescription')"
    />

    <!-- Offline State -->
    <MessageContent
      v-else-if="isOffline"
      icon="multiroom"
      :title="t('multiroom.systemOffline', { name: clientDisplayName })"
      :subtitle="t('multiroom.systemOfflineDescription', { ip: client?.ip || 'Unknown' })"
      :cta-label="deleting ? t('common.deleting') : t('multiroom.deleteSystem')"
      cta-variant="important"
      :cta-click="handleDelete"
    />

    <!-- Online State - Settings -->
    <template v-else>
      <!-- Speaker Name Input -->
      <SettingsSection :title="t(client?.is_local ? 'multiroom.systemNameMain' : 'multiroom.systemNameRemote')">
        <InputText v-model="clientName" :placeholder="client?.host" size="medium" :maxlength="16"
          @blur="saveClientName" />
      </SettingsSection>

      <!-- Audio Card Selection (remote clients only) -->
      <SettingsSection v-if="!client?.is_local && (isLoadingAudio || audioCardOptions.length > 0)" :title="t('multiroom.pending.audioCard')">
        <div class="hardware-row">
          <span class="hardware-row__label text-mono">{{ t('hardwareSettings.audioCardModel') }}</span>
          <div v-if="isLoadingAudio" class="skeleton-dropdown">
            <span class="skeleton-dropdown__text shimmer"></span>
          </div>
          <Dropdown
            v-else
            :model-value="selectedAudioId"
            :options="audioCardOptions"
            :disabled="isApplying"
            @change="selectAudioCard"
          />
        </div>
        <!-- External amplifier toggle (DAC cards only) -->
        <ListItemButton
          v-if="isDacCard"
          :title="t('multiroom.externalVolume')"
          :subtitle="t('volumeSettings.externalAmplifier')"
          variant="background"
          action="toggle"
          :model-value="!volumeControl"
          @click="toggleVolumeControl"
        />

        <p v-if="audioError" class="audio-error text-mono">{{ audioError }}</p>
      </SettingsSection>

      <!-- Speaker Type Selection -->
      <SettingsSection :title="t('multiroom.systemType')">
        <div class="speaker-types">
          <ListItemButton v-for="type in speakerTypes" :key="type.value" :title="type.label" variant="background"
            action="radio" icon-variant="standard" :model-value="selectedSpeakerType === type.value"
            @click="selectSpeakerType(type.value)">
            <template #icon>
              <SvgIcon :name="type.icon" :size="28" />
            </template>
          </ListItemButton>
        </div>

        <!-- Crossover Info Section -->
        <div v-if="showCrossoverInfo" class="crossover-info">
          <!-- Case 1: Subwoofer not in zone -->
          <template v-if="isSubwoofer && !isInZone">
            <p class="text-mono">
              {{ t('multiroom.crossover.subwooferNotInZone') }}
            </p>
          </template>

          <!-- Case 2: Subwoofer in zone -->
          <template v-else-if="isSubwoofer && isInZone">
            <h3 class="info-title heading-4">{{ t('multiroom.crossover.lowpassActive') }}</h3>
            <SettingItem :label="t('multiroom.crossover.crossoverFrequency')">
              <RangeSlider v-model="crossoverFrequency" :min="40" :max="200" :step="5" value-unit="Hz"
                @change="handleCrossoverChange" />
            </SettingItem>
            <p class="crossover-warning text-mono">{{ t('multiroom.crossover.disablePhysicalCrossover') }}</p>
          </template>

          <!-- Case 3: Non-subwoofer in zone with subwoofer -->
          <template v-else-if="!isSubwoofer && isInZone && zoneHasSubwoofer">
            <h3 class="info-title heading-4">{{ t('multiroom.crossover.highpassActive') }}</h3>
            <p class="text-mono">{{ t('multiroom.crossover.highpassDescription', { freq: zoneCrossoverFrequency }) }}</p>
          </template>
        </div>
      </SettingsSection>

      <!-- Client Info -->
      <SettingsSection :title="t('multiroom.systemInfo')">
        <div class="info-grid">
          <div class="info-item">
            <span class="info-label text-mono">{{ t('clientDetails.hostname') }}</span>
            <span class="info-value text-mono">{{ client?.host }}</span>
          </div>
          <div class="info-item">
            <span class="info-label text-mono">{{ t('clientDetails.ipAddress') }}</span>
            <span class="info-value text-mono">{{ client?.ip || 'Unknown' }}</span>
          </div>
        </div>
      </SettingsSection>

      <!-- Apply & Reboot (two-step confirm, only when audio card changed) -->
      <Button
        v-if="isAudioDirty"
        :variant="confirmReboot ? 'important' : 'brand'"
        size="medium"
        class="apply-button-sticky"
        :loading="isApplying"
        :disabled="isApplying"
        @click="handleApply"
      >
        {{ applyButtonLabel }}
      </Button>
    </template>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSnapcastStore } from '@/stores/snapcastStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useEqualizerStore } from '@/stores/equalizerStore';
import { useHardwareConfig } from '@/composables/useHardwareConfig';
import { logger } from '@/services/logger';
import InputText from '@/components/ui/InputText.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import Button from '@/components/ui/Button.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SettingItem from '@/components/settings/SettingItem.vue';

const props = defineProps({
  macId: {
    type: String,
    required: true
  }
});

const emit = defineEmits(['back']);

const { t } = useI18n();
const snapcastStore = useSnapcastStore();
const multiroomClientStore = useMultiroomStore();
const equalizerStore = useEqualizerStore();
const { loadHardwareConfig } = useHardwareConfig();

const clientName = ref('');
const originalClientName = ref('');
const selectedSpeakerType = ref('bookshelf');
const volumeControl = ref(true);
const deleting = ref(false);
const crossoverFrequency = ref(80);

// Find client by mac_id
const client = computed(() =>
  snapcastStore.clients.find(c => c.mac_id === props.macId)
);

// Audio card state
const isLoadingAudio = ref(!!(client.value && !client.value.is_local && client.value.online));
const audioCards = ref([]);
const selectedAudioId = ref('');
const savedAudioId = ref('');
const confirmReboot = ref(false);
const audioError = ref('');
const isApplying = ref(false);
const isRebooting = ref(false);
const rebootTimedOut = ref(false);
let rebootTimeoutId = null;
const REBOOT_TIMEOUT_MS = 120000;

// Filter out "none" from audio card options
const audioCardOptions = computed(() =>
  audioCards.value.filter(card => card.value !== 'none')
);

// Check if the selected audio card is a DAC (show volume control toggle)
const isDacCard = computed(() => {
  if (!selectedAudioId.value) return false;
  const card = audioCards.value.find(c => c.value === selectedAudioId.value);
  return card?.category === 'dac';
});

// Dirty check for audio card change
const isAudioDirty = computed(() =>
  selectedAudioId.value && savedAudioId.value && selectedAudioId.value !== savedAudioId.value
);

// Check if client is offline
const isOffline = computed(() => {
  return client.value ? !client.value.online : true;
});

// Display name for offline message
const clientDisplayName = computed(() =>
  client.value?.name || client.value?.host || 'Unknown'
);

// Check if client is in a zone
const clientZone = computed(() => {
  return equalizerStore.getZoneGroup(props.macId);
});

const isInZone = computed(() => !!clientZone.value);

// Check if current speaker type is subwoofer
const isSubwoofer = computed(() => selectedSpeakerType.value === 'subwoofer');

// Check if zone contains a subwoofer
const zoneHasSubwoofer = computed(() => {
  if (!clientZone.value?.id) return false;
  return multiroomClientStore.hasOnlineSubwoofer(clientZone.value.id);
});

// Get zone crossover frequency for display (non-subwoofer clients)
const zoneCrossoverFrequency = computed(() => {
  return clientZone.value?.crossover_frequency || 80;
});

// Sync crossover frequency ref from zone data
watch(
  () => clientZone.value?.crossover_frequency,
  (newFreq) => {
    if (newFreq != null) {
      crossoverFrequency.value = newFreq;
    }
  },
  { immediate: true }
);

// Watch client coming back online after reboot
watch(
  () => client.value?.online,
  (online) => {
    if (isRebooting.value && online) {
      isRebooting.value = false;
      rebootTimedOut.value = false;
      if (rebootTimeoutId) clearTimeout(rebootTimeoutId);
      // Update saved audio id to reflect the new card
      savedAudioId.value = selectedAudioId.value;
    }
  }
);

// Show crossover info when relevant
const showCrossoverInfo = computed(() => {
  // Always show for subwoofer (different message if not in zone)
  if (isSubwoofer.value) return true;
  // Show for non-subwoofer in zone with subwoofer
  if (isInZone.value && zoneHasSubwoofer.value) return true;
  return false;
});

// Speaker type options
const speakerTypes = computed(() => [
  { value: 'satellite', label: t('multiroom.systemTypes.satellite'), icon: 'speakerSatellite' },
  { value: 'bookshelf', label: t('multiroom.systemTypes.bookshelf'), icon: 'speakerShelf' },
  { value: 'tower', label: t('multiroom.systemTypes.tower'), icon: 'speakerColumn' },
  { value: 'subwoofer', label: t('multiroom.systemTypes.subwoofer'), icon: 'speakerSub' }
]);

// Apply button label (two-step confirm)
const applyButtonLabel = computed(() => {
  if (isApplying.value) return t('multiroom.pending.applying');
  if (confirmReboot.value) return t('multiroom.pending.confirmReboot');
  return t('multiroom.pending.applyAndReboot');
});

function selectAudioCard(audioId) {
  selectedAudioId.value = audioId;
  confirmReboot.value = false;
  audioError.value = '';
  // Default volume_control based on card category (user can override via toggle)
  const card = audioCards.value.find(c => c.value === audioId);
  volumeControl.value = card?.category !== 'dac';
}

async function toggleVolumeControl() {
  volumeControl.value = !volumeControl.value;
  // If no pending audio card change, save immediately via PATCH
  if (!isAudioDirty.value) {
    try {
      await multiroomClientStore.updateClient(props.macId, { volume_control: volumeControl.value });
    } catch (error) {
      logger.error('multiroom', 'Error saving volume control', error);
      volumeControl.value = !volumeControl.value; // Revert on failure
    }
  }
}

function handleApply() {
  if (!confirmReboot.value) {
    confirmReboot.value = true;
    return;
  }
  applyAudioChange();
}

async function applyAudioChange() {
  if (!selectedAudioId.value || isApplying.value) return;

  isApplying.value = true;
  audioError.value = '';
  try {
    await multiroomClientStore.configureClientAudio(props.macId, selectedAudioId.value, volumeControl.value);
    isRebooting.value = true;
    rebootTimeoutId = setTimeout(() => {
      rebootTimedOut.value = true;
    }, REBOOT_TIMEOUT_MS);
  } catch (e) {
    audioError.value = e?.response?.data?.detail || t('multiroom.pending.errorGeneric');
    logger.error('multiroom', 'Error configuring client audio', e);
  } finally {
    isApplying.value = false;
    confirmReboot.value = false;
  }
}

async function handleCrossoverChange(frequency) {
  if (!clientZone.value?.id) return;
  try {
    await equalizerStore.setZoneCrossoverFrequency(clientZone.value.id, frequency);
  } catch (error) {
    console.error('Error updating crossover frequency:', error);
  }
}

async function selectSpeakerType(type) {
  if (type === selectedSpeakerType.value) return;

  selectedSpeakerType.value = type;

  if (type === 'subwoofer' && clientZone.value?.crossover_frequency != null) {
    crossoverFrequency.value = clientZone.value.crossover_frequency;
  }

  // Save immediately via PATCH /api/multiroom/clients/{mac_id}
  try {
    await multiroomClientStore.updateClient(props.macId, { speaker_type: type });
  } catch (error) {
    console.error('Error saving speaker type:', error);
  }
}

async function saveClientName() {
  const newName = clientName.value?.trim();
  if (!newName || newName === originalClientName.value) return;

  try {
    await multiroomClientStore.updateClient(props.macId, { name: newName });
    originalClientName.value = newName;
  } catch (error) {
    console.error('Error saving client name:', error);
  }
}

async function handleDelete() {
  if (deleting.value) return;

  deleting.value = true;
  try {
    const success = await multiroomClientStore.deleteClient(props.macId);
    if (success) {
      emit('back');
    }
  } catch (error) {
    console.error('Error deleting client:', error);
  } finally {
    deleting.value = false;
  }
}

onMounted(async () => {
  if (client.value) {
    clientName.value = client.value.name || client.value.host;
    originalClientName.value = clientName.value;
    selectedSpeakerType.value = client.value.speaker_type || equalizerStore.getClientSpeakerType(props.macId);
    volumeControl.value = client.value.volume_control !== false;

    // Load audio card options and current card for remote clients
    if (!client.value.is_local && client.value.online) {
      try {
        const [config, hardware] = await Promise.all([
          loadHardwareConfig(true),
          multiroomClientStore.fetchClientHardware(props.macId).catch(() => null),
        ]);

        if (config?.options?.audio_cards) {
          audioCards.value = config.options.audio_cards;
        }
        if (hardware?.audio?.id) {
          selectedAudioId.value = hardware.audio.id;
          savedAudioId.value = hardware.audio.id;
        }
      } finally {
        isLoadingAudio.value = false;
      }
    } else {
      isLoadingAudio.value = false;
    }
  }
});

onUnmounted(() => {
  if (rebootTimeoutId) clearTimeout(rebootTimeoutId);
});
</script>

<style scoped>
.client-edit {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.hardware-row {
  display: flex;
  align-items: baseline;
  gap: var(--space-03);
}

.hardware-row__label {
  color: var(--color-text-secondary);
  width: 33%;
  flex-shrink: 0;
}

.hardware-row :deep(.dropdown) {
  flex: 1;
}

.skeleton-dropdown {
  flex: 1;
  display: flex;
  align-items: center;
  padding: var(--space-03) var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-background-neutral);
  box-shadow: inset 0 0 0 2px var(--color-border);
}

.skeleton-dropdown::before {
  content: '\200b';
  font-family: 'Neue Montreal Medium', 'Noto Sans SC', sans-serif;
  font-size: var(--font-size-h3);
  line-height: var(--line-height-h3);
}

.skeleton-dropdown__text {
  width: 60%;
  height: var(--line-height-h3);
  border-radius: var(--radius-02);
  --shimmer-base: var(--color-background-strong);
  --shimmer-highlight: var(--color-background-medium-16);
}

.audio-error {
  color: var(--color-error, #ef4444);
  margin: 0;
}

.speaker-types {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-01);
}

.crossover-info {
  background: var(--color-background-strong);
  border-radius: var(--radius-04);
  padding: var(--space-04);
  margin-top: var(--space-03);
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.crossover-info .info-title {
  color: var(--color-text);
  margin: 0;
}

.crossover-info :deep(.slider-container.horizontal .range-track) {
  background: linear-gradient(to right,
    var(--slider-accent) 0%,
    var(--slider-accent) var(--progress),
    var(--color-background-neutral) var(--progress),
    var(--color-background-neutral) 100%);
}

.crossover-info p {
  color: var(--color-text-secondary);
  margin: 0;
}
.crossover-warning {
  padding-top: var(--space-01);
}

.info-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-02);
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

.apply-button-sticky {
  position: sticky;
  bottom: 0;
  width: 100%;
  z-index: 10;
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .hardware-row {
    flex-direction: column;
    align-items: stretch;
  }

  .hardware-row__label {
    width: auto;
  }

  .speaker-types {
    grid-template-columns: 1fr;
  }

  .info-grid {
    grid-template-columns: 1fr;
  }
}
</style>
