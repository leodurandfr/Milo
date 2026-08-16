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
          :placeholder="t('common.selectOption')"
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

    <!-- Screen (optional) -->
    <ToggleSection
      :title="t('hardwareSettings.screen')"
      :enabled="hasScreen"
      @change="toggleScreen"
    >
      <div class="hardware-row">
        <span class="hardware-row__label text-mono">{{ t('hardwareSettings.screenModel') }}</span>
        <Dropdown
          :model-value="config.screen_type"
          :options="screenOptionsFiltered"
          :disabled="isRebooting"
          placeholder=""
          @change="onScreenChange"
        />
      </div>
    </ToggleSection>

    <!-- Rotary Encoder (optional) -->
    <ToggleSection
      :title="t('hardwareSettings.rotaryEncoder')"
      :enabled="config.rotary_enabled"
      @change="toggleRotary"
    >
      <div class="encoder-pins">
        <SettingItem label="CLK">
          <Dropdown
            :model-value="config.clk_pin"
            :options="gpioPinOptions"
            :disabled="isRebooting"
            :placeholder="t('common.selectOption')"
            @change="v => onPinChange('clk_pin', v)"
          />
        </SettingItem>
        <SettingItem label="DT">
          <Dropdown
            :model-value="config.dt_pin"
            :options="gpioPinOptions"
            :disabled="isRebooting"
            :placeholder="t('common.selectOption')"
            @change="v => onPinChange('dt_pin', v)"
          />
        </SettingItem>
        <SettingItem label="SW">
          <Dropdown
            :model-value="config.sw_pin"
            :options="gpioPinOptions"
            :disabled="isRebooting"
            :placeholder="t('common.selectOption')"
            @change="v => onPinChange('sw_pin', v)"
          />
        </SettingItem>
      </div>
    </ToggleSection>

    <!-- IR Remote receiver (TSOP4838) -->
    <ToggleSection
      :title="t('hardwareSettings.irRemote')"
      :enabled="config.ir_enabled"
      @change="toggleIrRemote"
    >
      <div class="encoder-pins">
        <SettingItem label="OUT">
          <Dropdown
            :model-value="config.ir_gpio_pin"
            :options="gpioPinOptions"
            :disabled="isRebooting"
            :placeholder="t('common.selectOption')"
            @change="v => onIrPinChange(v)"
          />
        </SettingItem>
        <SettingItem label="VCC">
          <div class="ir-fixed-pin">
            <Dropdown
              :model-value="'3.3V'"
              :options="[{ label: '3.3V', value: '3.3V' }]"
              disabled
            />
          </div>
        </SettingItem>
        <SettingItem label="GND">
          <div class="ir-fixed-pin">
            <Dropdown
              :model-value="'GND'"
              :options="[{ label: 'GND', value: 'GND' }]"
              disabled
            />
          </div>
        </SettingItem>
      </div>
    </ToggleSection>

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
import { useTimer } from '@/composables/useTimer';
import { apiCall } from '@/services/apiCall';
import { logger } from '@/services/logger';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import ToggleSection from '@/components/ui/ToggleSection.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import Button from '@/components/ui/Button.vue';

const { t } = useI18n();
const { loadHardwareConfig, hardwareConfig } = useHardwareConfig();
const timer = useTimer();

// GPIO pin dropdown options — sourced from the backend (GET /hardware-config)
// so the selectable range stays the single source of truth shared with the
// rotary/IR pin validators and can never offer a pin the backend rejects (422).
// Populated in syncFromData(), like audioCardOptions / screenOptions.
const gpioPinOptions = ref([]);

// Local config for instant UI responsiveness
const config = ref({
  audio_id: '',
  volume_control: true,
  screen_type: 'none',
  rotary_enabled: true,
  clk_pin: 22,
  dt_pin: 27,
  sw_pin: 23,
  ir_enabled: true,
  ir_gpio_pin: 17,
});

// Saved config (for dirty check)
const savedConfig = ref(null);

const audioCardOptions = ref([]);
const screenOptions = ref([]);

const confirmReboot = ref(false);
const isApplying = ref(false);
const isRebooting = ref(false);

const isDirty = computed(() => {
  if (!savedConfig.value) return false;
  return (
    config.value.audio_id !== savedConfig.value.audio_id ||
    config.value.screen_type !== savedConfig.value.screen_type ||
    config.value.rotary_enabled !== savedConfig.value.rotary_enabled ||
    config.value.clk_pin !== savedConfig.value.clk_pin ||
    config.value.dt_pin !== savedConfig.value.dt_pin ||
    config.value.sw_pin !== savedConfig.value.sw_pin ||
    config.value.ir_enabled !== savedConfig.value.ir_enabled ||
    config.value.ir_gpio_pin !== savedConfig.value.ir_gpio_pin
  );
});

// Check if the selected audio card is a DAC
const isDacCard = computed(() => {
  if (!config.value.audio_id) return false;
  const card = audioCardOptions.value.find(c => c.value === config.value.audio_id);
  return card?.category === 'dac';
});

// Screen: toggle ON/OFF (replaces "none" option in dropdown)
const hasScreen = computed(() => config.value.screen_type !== 'none');
const screenOptionsFiltered = computed(() => screenOptions.value.filter(s => s.value !== 'none'));
const lastScreenType = ref(null);

function toggleScreen(enabled) {
  confirmReboot.value = false;
  if (enabled) {
    config.value.screen_type = lastScreenType.value || screenOptionsFiltered.value[0]?.value || 'none';
  } else {
    lastScreenType.value = config.value.screen_type;
    config.value.screen_type = 'none';
  }
}

function toggleRotary(enabled) {
  config.value.rotary_enabled = enabled;
  confirmReboot.value = false;
}

function syncFromData(data) {
  const current = data.current;
  const snapshot = {
    audio_id: current.audio?.id || '',
    volume_control: current.audio?.volume_control !== false,
    screen_type: current.screen?.type || 'none',
    rotary_enabled: current.rotary_encoder?.enabled !== false,
    clk_pin: current.rotary_encoder?.clk_pin ?? 22,
    dt_pin: current.rotary_encoder?.dt_pin ?? 27,
    sw_pin: current.rotary_encoder?.sw_pin ?? 23,
    ir_enabled: current.ir_remote?.enabled !== false,
    ir_gpio_pin: current.ir_remote?.gpio_pin ?? 17,
  };
  config.value = { ...snapshot };
  savedConfig.value = { ...snapshot };

  // Remember last non-none screen type for toggle restore
  if (snapshot.screen_type !== 'none') {
    lastScreenType.value = snapshot.screen_type;
  }

  audioCardOptions.value = data.options.audio_cards;
  screenOptions.value = data.options.screens;
  gpioPinOptions.value = data.options.gpio_pins;
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
    const result = await apiCall.patch('/api/volume/volume-control', { volume_control: config.value.volume_control }, {
      category: 'hardware',
      message: 'Error saving volume control'
    });
    if (!result.ok) {
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

function onIrPinChange(value) {
  config.value.ir_gpio_pin = value;
  confirmReboot.value = false;
}

function toggleIrRemote(enabled) {
  config.value.ir_enabled = enabled;
  confirmReboot.value = false;
}

async function applyAndReboot() {
  isApplying.value = true;
  confirmReboot.value = false;

  const payload = {
    audio: { id: config.value.audio_id, volume_control: config.value.volume_control },
    screen: { type: config.value.screen_type },
    rotary_encoder: {
      enabled: config.value.rotary_enabled,
      clk_pin: config.value.clk_pin,
      dt_pin: config.value.dt_pin,
      sw_pin: config.value.sw_pin,
    },
    ir_remote: {
      enabled: config.value.ir_enabled,
      gpio_pin: config.value.ir_gpio_pin,
    },
  };

  const putResult = await apiCall.put('/api/settings/hardware-config', payload, {
    category: 'hardware',
    message: 'Failed to apply hardware config'
  });
  if (!putResult.ok) {
    isApplying.value = false;
    return;
  }
  isApplying.value = false;
  isRebooting.value = true;

  // Poll for backend to come back after reboot (max ~3 minutes).
  // Use debug log level so the expected stream of failures during reboot does
  // not flood the console.
  let pollCount = 0;
  const maxPolls = 60;
  const pollInterval = timer.setInterval(async () => {
    pollCount++;
    if (pollCount > maxPolls) {
      timer.clear(pollInterval);
      isRebooting.value = false;
      logger.error('hardware', 'Reboot polling timed out');
      return;
    }
    const pingResult = await apiCall.get('/api/ping', {
      category: 'hardware',
      message: 'Reboot polling ping failed',
      timeout: 2000,
      logLevel: 'debug'
    });
    if (pingResult.ok) {
      timer.clear(pollInterval);
      window.location.reload();
    }
  }, 3000);
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

.encoder-pins {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: var(--space-03);
}

/* IR receiver: VCC and GND are physical rails, not configurable GPIOs.
   They are rendered as visually consistent disabled dropdowns with the caret
   removed so they don't suggest a hidden option list. */
.ir-fixed-pin :deep(.dropdown-icon) {
  display: none;
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
