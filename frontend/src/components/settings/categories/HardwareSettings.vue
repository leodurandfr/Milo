<!-- frontend/src/components/settings/categories/HardwareSettings.vue -->
<template>
  <SettingsContainer>
    <!-- Audio Card -->
    <SettingsSection :title="t('hardwareSettings.audioCard')">
      <div class="hardware-row">
        <span class="hardware-row__label text-mono">{{ t('hardwareSettings.audioCardModel') }}</span>
        <Dropdown
          :model-value="config.audio_id"
          :options="audioCardOptions"
          :disabled="isRebooting"
          @change="onAudioChange"
        />
      </div>
    </SettingsSection>

    <!-- Screen -->
    <SettingsSection :title="t('hardwareSettings.screen')">
      <div class="hardware-row">
        <span class="hardware-row__label text-mono">{{ t('hardwareSettings.screenModel') }}</span>
        <Dropdown
          :model-value="config.screen_type"
          :options="screenOptions"
          :disabled="isRebooting"
          @change="onScreenChange"
        />
      </div>
    </SettingsSection>

    <!-- Rotary Encoder -->
    <SettingsSection :title="t('hardwareSettings.rotaryEncoder')">
      <p class="text-mono encoder-description">{{ t('hardwareSettings.rotaryEncoderDescription') }}</p>
      <div class="encoder-pins">
        <SettingItem label="CLK">
          <Dropdown
            :model-value="config.clk_pin"
            :options="gpioPinOptions"
            :disabled="isRebooting"
            @change="v => onPinChange('clk_pin', v)"
          />
        </SettingItem>
        <SettingItem label="DT">
          <Dropdown
            :model-value="config.dt_pin"
            :options="gpioPinOptions"
            :disabled="isRebooting"
            @change="v => onPinChange('dt_pin', v)"
          />
        </SettingItem>
      </div>
    </SettingsSection>

    <!-- Apply & Reboot (sticky, visible only when dirty) -->
    <Button v-if="isDirty && !isRebooting" variant="brand" class="apply-button-sticky"
      :loading="isApplying" @click="showConfirm = true">
      {{ t('hardwareSettings.applyAndReboot') }}
    </Button>

    <!-- Confirm dialog -->
    <div v-if="showConfirm && !isRebooting" class="confirm-section">
      <p class="confirm-message text-mono">{{ t('hardwareSettings.confirmMessage') }}</p>
      <div class="confirm-actions">
        <Button variant="background-strong" @click="showConfirm = false">
          {{ t('common.cancel') }}
        </Button>
        <Button variant="important" :loading="isApplying" @click="applyAndReboot">
          {{ t('common.confirm') }}
        </Button>
      </div>
    </div>

    <!-- Rebooting overlay -->
    <div v-if="isRebooting" class="rebooting-section">
      <LoadingSpinner />
      <p class="heading-3">{{ t('hardwareSettings.rebooting') }}</p>
      <p class="text-mono text-secondary">{{ t('hardwareSettings.rebootingDescription') }}</p>
    </div>
  </SettingsContainer>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useHardwareConfig } from '@/composables/useHardwareConfig';
import axios from 'axios';
import { logger } from '@/services/logger';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import Button from '@/components/ui/Button.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';

const { t } = useI18n();
const { loadHardwareConfig } = useHardwareConfig();

// GPIO pin options (1–40 for RPi 40-pin header)
const gpioPinOptions = Array.from({ length: 40 }, (_, i) => ({
  label: `GPIO ${i + 1}`,
  value: i + 1
}));

// Local config for instant UI responsiveness
// sw_pin is kept internally for the backend payload but not shown in the UI
const config = ref({
  audio_id: '',
  screen_type: 'none',
  clk_pin: 22,
  dt_pin: 27,
  sw_pin: 23,
});

// Saved config (for dirty check)
const savedConfig = ref(null);

// Dropdown options from the registry
const audioCardOptions = ref([]);
const screenOptions = ref([]);

// UI state
const showConfirm = ref(false);
const isApplying = ref(false);
const isRebooting = ref(false);

const isDirty = computed(() => {
  if (!savedConfig.value) return false;
  return (
    config.value.audio_id !== savedConfig.value.audio_id ||
    config.value.screen_type !== savedConfig.value.screen_type ||
    config.value.clk_pin !== savedConfig.value.clk_pin ||
    config.value.dt_pin !== savedConfig.value.dt_pin
  );
});

function syncFromData(data) {
  const current = data.current;
  const snapshot = {
    audio_id: current.audio?.id || '',
    screen_type: current.screen?.type || 'none',
    clk_pin: current.rotary_encoder?.clk_pin ?? 22,
    dt_pin: current.rotary_encoder?.dt_pin ?? 27,
    sw_pin: current.rotary_encoder?.sw_pin ?? 23,
  };
  config.value = { ...snapshot };
  savedConfig.value = { ...snapshot };

  audioCardOptions.value = data.options.audio_cards;
  screenOptions.value = data.options.screens;
}

function onAudioChange(value) {
  config.value.audio_id = value;
  showConfirm.value = false;
}

function onScreenChange(value) {
  config.value.screen_type = value;
  showConfirm.value = false;
}

function onPinChange(pin, value) {
  config.value[pin] = value;
  showConfirm.value = false;
}

async function applyAndReboot() {
  isApplying.value = true;
  showConfirm.value = false;

  try {
    const payload = {
      audio: { id: config.value.audio_id },
      screen: { type: config.value.screen_type },
      rotary_encoder: {
        clk_pin: config.value.clk_pin,
        dt_pin: config.value.dt_pin,
        sw_pin: config.value.sw_pin,
      },
    };

    await axios.put('/api/settings/hardware-config', payload);
    isApplying.value = false;
    isRebooting.value = true;

    // Poll for backend to come back after reboot (max ~3 minutes)
    let pollCount = 0;
    const maxPolls = 60;
    const pollInterval = setInterval(async () => {
      pollCount++;
      if (pollCount > maxPolls) {
        clearInterval(pollInterval);
        isRebooting.value = false;
        logger.error('hardware', 'Reboot polling timed out');
        return;
      }
      try {
        await axios.get('/api/ping', { timeout: 2000 });
        clearInterval(pollInterval);
        window.location.reload();
      } catch {
        // Backend still down, keep polling
      }
    }, 3000);
  } catch (err) {
    logger.error('hardware', 'Failed to apply hardware config', err);
    isApplying.value = false;
  }
}

onMounted(async () => {
  const data = await loadHardwareConfig(true);
  if (data) {
    syncFromData(data);
  }
});
</script>

<style scoped>
/* Desktop: label left (33%), control right */
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

/* Rotary encoder */
.encoder-description {
  color: var(--color-text-secondary);
}

.encoder-pins {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-03);
}

/* Sticky apply button (matches MultiroomSettings pattern) */
.apply-button-sticky {
  position: sticky;
  bottom: 0;
  width: 100%;
  z-index: 10;
}

/* Confirm section */
.confirm-section {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-03);
}

.confirm-message {
  color: var(--color-text-secondary);
  text-align: center;
}

.confirm-actions {
  display: flex;
  gap: var(--space-03);
}

.rebooting-section {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-06) 0;
  text-align: center;
}

/* Mobile: stack label/control vertically */
@media (max-aspect-ratio: 4/3) {
  .hardware-row {
    flex-direction: column;
    align-items: stretch;
  }

  .hardware-row__label {
    width: auto;
  }

  .encoder-pins {
    grid-template-columns: 1fr;
  }
}
</style>
