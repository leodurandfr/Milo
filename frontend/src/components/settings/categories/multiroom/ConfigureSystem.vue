<!-- frontend/src/components/settings/categories/multiroom/ConfigureSystem.vue -->
<!-- Form for configuring a pending (unconfigured) system -->
<template>
  <div class="configure-speaker">
    <!-- Rebooting State -->
    <MessageContent
      v-if="isRebooting"
      icon="multiroom"
      :loading="!rebootTimedOut"
      :loading-delay="0"
      :title="t('multiroom.pending.rebootingMessage')"
      :subtitle="rebootTimedOut ? t('multiroom.pending.rebootTimeout') : t('multiroom.pending.rebootingDescription')"
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

      <!-- Speaker Info -->
      <SettingsSection :title="t('multiroom.systemInfo')">
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
        :disabled="!selectedAudioId"
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
import { useHardwareConfig } from '@/composables/useHardwareConfig';
import InputText from '@/components/ui/InputText.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import Button from '@/components/ui/Button.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';

const props = defineProps({
  macId: {
    type: String,
    required: true,
  },
});

const emit = defineEmits(['back']);

const { t } = useI18n();
const multiroomStore = useMultiroomStore();
const { loadHardwareConfig } = useHardwareConfig();

// Form state
const speakerName = ref('');
const selectedAudioId = ref('');
const selectedSpeakerType = ref('bookshelf');

// UI state
const isRebooting = ref(false);
const rebootTimedOut = ref(false);
const confirmReboot = ref(false);
const error = ref('');
const audioCards = ref([]);
let rebootTimeoutId = null;
const REBOOT_TIMEOUT_MS = 120000; // 2 minutes

// Reactive reference to the pending client
const pendingClient = computed(() => multiroomStore.pendingClients.get(props.macId));

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

// Watch for the pending client being removed (means it appeared in Snapcast)
const stopWatch = watch(
  () => multiroomStore.pendingClients.get(props.macId),
  (newVal) => {
    if (isRebooting.value && !newVal) {
      // Client moved from pending to real registry — success
      emit('back');
    }
  },
);

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
  if (!selectedAudioId.value) return;

  isRebooting.value = true;
  error.value = '';

  try {
    await multiroomStore.configurePendingClient(props.macId, {
      name: speakerName.value.trim() || null,
      speaker_type: selectedSpeakerType.value,
      audio_id: selectedAudioId.value,
    });
    rebootTimeoutId = setTimeout(() => {
      rebootTimedOut.value = true;
    }, REBOOT_TIMEOUT_MS);
  } catch (e) {
    isRebooting.value = false;
    error.value = e?.response?.data?.detail || t('multiroom.pending.errorGeneric');
  }
}

onMounted(async () => {
  // Load audio card options from hardware registry
  const loaded = await loadAudioCards();
  if (!loaded) return;

  // Pre-fill from pending client data
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
