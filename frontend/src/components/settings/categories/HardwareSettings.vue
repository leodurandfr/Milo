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

      <!-- Volume management toggle (DAC cards only) -->
      <ListItemButton
        v-if="isDacCard"
        :title="t('volumeSettings.volumeManagement')"
        variant="background"
        action="toggle"
        :model-value="config.volume_control"
        @click="toggleVolumeControl"
      />
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
        <SettingItem label="SW">
          <Dropdown
            :model-value="config.sw_pin"
            :options="gpioPinOptions"
            :disabled="isRebooting"
            @change="v => onPinChange('sw_pin', v)"
          />
        </SettingItem>
      </div>
    </SettingsSection>

    <!-- Apply & Reboot (sticky, two-step confirm) -->
    <Button v-if="isDirty || isRebooting" :variant="confirmReboot ? 'important' : 'brand'" class="apply-button-sticky"
      :loading="isApplying || isRebooting" :disabled="isApplying || isRebooting" @click="handleApply">
      {{ applyButtonLabel }}
    </Button>
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
import ListItemButton from '@/components/ui/ListItemButton.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import Button from '@/components/ui/Button.vue';

const { t } = useI18n();
const { loadHardwareConfig, hardwareConfig } = useHardwareConfig();

// GPIO pin options (1–40 for RPi 40-pin header)
const gpioPinOptions = Array.from({ length: 40 }, (_, i) => ({
  label: `GPIO ${i + 1}`,
  value: i + 1
}));

// Local config for instant UI responsiveness
const config = ref({
  audio_id: '',
  volume_control: true,
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
const confirmReboot = ref(false);
const isApplying = ref(false);
const isRebooting = ref(false);

const isDirty = computed(() => {
  if (!savedConfig.value) return false;
  return (
    config.value.audio_id !== savedConfig.value.audio_id ||
    config.value.screen_type !== savedConfig.value.screen_type ||
    config.value.clk_pin !== savedConfig.value.clk_pin ||
    config.value.dt_pin !== savedConfig.value.dt_pin ||
    config.value.sw_pin !== savedConfig.value.sw_pin
  );
});

// Check if the selected audio card is a DAC
const isDacCard = computed(() => {
  if (!config.value.audio_id) return false;
  const card = audioCardOptions.value.find(c => c.value === config.value.audio_id);
  return card?.category === 'dac';
});

function syncFromData(data) {
  const current = data.current;
  const snapshot = {
    audio_id: current.audio?.id || '',
    volume_control: current.audio?.volume_control !== false,
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

const applyButtonLabel = computed(() => {
  if (isRebooting.value) return t('hardwareSettings.rebooting');
  if (confirmReboot.value) return t('hardwareSettings.confirmReboot');
  return t('hardwareSettings.applyAndReboot');
});

function handleApply() {
  if (!confirmReboot.value) {
    confirmReboot.value = true;
    return;
  }
  applyAndReboot();
}

function onAudioChange(value) {
  config.value.audio_id = value;
  confirmReboot.value = false;
  // Default volume_control based on card category (user can override via toggle)
  const card = audioCardOptions.value.find(c => c.value === value);
  config.value.volume_control = card?.category !== 'dac';
}

async function toggleVolumeControl() {
  config.value.volume_control = !config.value.volume_control;
  // If no pending hardware change, save immediately via API
  if (!isDirty.value) {
    try {
      await axios.patch('/api/volume/volume-control', { volume_control: config.value.volume_control });
    } catch (error) {
      logger.error('hardware', 'Error saving volume control', error);
      config.value.volume_control = !config.value.volume_control; // Revert on failure
    }
  }
}

function onScreenChange(value) {
  config.value.screen_type = value;
  confirmReboot.value = false;
}

function onPinChange(pin, value) {
  config.value[pin] = value;
  confirmReboot.value = false;
}

async function applyAndReboot() {
  isApplying.value = true;
  confirmReboot.value = false;

  try {
    const payload = {
      audio: { id: config.value.audio_id, volume_control: config.value.volume_control },
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

// Use preloaded data immediately for correct layout on first render
if (hardwareConfig.value) {
  syncFromData(hardwareConfig.value);
}

onMounted(async () => {
  // Fresh reload to ensure data is up-to-date
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
  grid-template-columns: 1fr 1fr 1fr;
  gap: var(--space-03);
}

/* Sticky apply button (matches MultiroomSettings pattern) */
.apply-button-sticky {
  position: sticky;
  bottom: 0;
  width: 100%;
  z-index: 10;
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
