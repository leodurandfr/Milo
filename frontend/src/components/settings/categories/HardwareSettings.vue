<!-- frontend/src/components/settings/categories/HardwareSettings.vue -->
<template>
  <SettingsContainer>
    <!-- Live, outside isDirty: the fan never waits for Apply & Reboot. -->
    <FanSection v-if="fanStore.available" />

    <!-- What is wired to the board, one card: groups apart by a rule.
         Not four ToggleSections — nested, each would draw a card in a card. -->
    <SettingsSection>
      <div class="hardware-groups">
        <div class="hardware-group">
          <h3 class="heading-3">{{ t('hardwareSettings.audioCard') }}</h3>
          <SettingItem :label="t('hardwareSettings.audioCardModel')" inline>
            <Dropdown
              :model-value="config.audio_id"
              :options="audioCardOptions"
              :disabled="isRebooting"
              :placeholder="t('common.selectOption')"
              @change="onAudioChange"
            />
          </SettingItem>

          <!-- Volume management toggle (DAC cards only) -->
          <ListItemButton
            v-if="isDacCard"
            :title="t('volumeSettings.volumeManagement')"
            action="toggle"
            :model-value="config.volume_control"
            @click="toggleVolumeControl"
          />
        </div>

        <div class="hardware-divider"></div>

        <div class="hardware-group">
          <div class="hardware-group__header">
            <h3 class="heading-3">{{ t('hardwareSettings.screen') }}</h3>
            <Toggle :model-value="hasScreen" :disabled="isRebooting" @change="toggleScreen" />
          </div>
          <SettingItem v-if="hasScreen" :label="t('hardwareSettings.screenModel')" inline>
            <Dropdown
              :model-value="config.screen_type"
              :options="screenOptionsFiltered"
              :disabled="isRebooting"
              placeholder=""
              @change="onScreenChange"
            />
          </SettingItem>
        </div>

        <div class="hardware-divider"></div>

        <div class="hardware-group">
          <div class="hardware-group__header">
            <h3 class="heading-3">{{ t('hardwareSettings.rotaryEncoder') }}</h3>
            <Toggle :model-value="config.rotary_enabled" :disabled="isRebooting" @change="toggleRotary" />
          </div>
          <div v-if="config.rotary_enabled" class="encoder-pins">
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
        </div>

        <div class="hardware-divider"></div>

        <!-- IR Remote receiver (TSOP4838) -->
        <div class="hardware-group">
          <div class="hardware-group__header">
            <h3 class="heading-3">{{ t('hardwareSettings.irRemote') }}</h3>
            <Toggle :model-value="config.ir_enabled" :disabled="isRebooting" @change="toggleIrRemote" />
          </div>
          <div v-if="config.ir_enabled" class="encoder-pins">
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
              <div class="fixed-pin">
                <Dropdown
                  :model-value="'3.3V'"
                  :options="[{ label: '3.3V', value: '3.3V' }]"
                  disabled
                />
              </div>
            </SettingItem>
            <SettingItem label="GND">
              <div class="fixed-pin">
                <Dropdown
                  :model-value="'GND'"
                  :options="[{ label: 'GND', value: 'GND' }]"
                  disabled
                />
              </div>
            </SettingItem>
          </div>
        </div>

        <!-- Pi 5 only: no other board's bootloader can wait for its button. -->
        <template v-if="powerButtonSupported">
          <div class="hardware-divider"></div>

          <div class="hardware-group">
            <div class="hardware-group__header">
              <h3 class="heading-3">{{ t('hardwareSettings.powerButton') }}</h3>
              <Toggle :model-value="config.power_button_enabled" :disabled="isRebooting"
                @change="togglePowerButton" />
            </div>
            <span class="hardware-description text-body">
              {{ t('hardwareSettings.powerButtonDescription') }}
            </span>
            <div v-if="config.power_button_enabled" class="encoder-pins">
              <SettingItem label="LED −">
                <Dropdown
                  :model-value="config.power_led_gpio_pin"
                  :options="gpioPinOptions"
                  :disabled="isRebooting"
                  :placeholder="t('common.selectOption')"
                  @change="onPowerLedPinChange"
                />
              </SettingItem>
              <SettingItem label="LED +">
                <div class="fixed-pin">
                  <Dropdown :model-value="'5V'" :options="[{ label: '5V', value: '5V' }]" disabled />
                </div>
              </SettingItem>
              <!-- J2: the PMIC's own input on the Pi 5 board, not a GPIO. -->
              <SettingItem label="BTN">
                <div class="fixed-pin">
                  <Dropdown :model-value="'J2'" :options="[{ label: 'J2', value: 'J2' }]" disabled />
                </div>
              </SettingItem>
            </div>
          </div>
        </template>
      </div>
    </SettingsSection>

    <!-- Apply & Reboot (sticky, two-step confirm) -->
    <Button v-if="isDirty || isRebooting" :variant="confirmReboot ? 'important' : 'brand'" class="apply-button-sticky" floating
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
import ListItemButton from '@/components/ui/ListItemButton.vue';
import Toggle from '@/components/ui/Toggle.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import Button from '@/components/ui/Button.vue';
import FanSection from '@/components/settings/categories/FanSection.vue';
import { useFanStore } from '@/stores/fanStore';

const { t } = useI18n();
const fanStore = useFanStore();
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
  power_button_enabled: false,
  power_led_gpio_pin: 26,
});

// Saved config (for dirty check)
const savedConfig = ref(null);

const audioCardOptions = ref([]);
const screenOptions = ref([]);
const powerButtonSupported = ref(false);

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
    config.value.ir_gpio_pin !== savedConfig.value.ir_gpio_pin ||
    config.value.power_button_enabled !== savedConfig.value.power_button_enabled ||
    config.value.power_led_gpio_pin !== savedConfig.value.power_led_gpio_pin
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
    power_button_enabled: current.power_button.enabled,
    power_led_gpio_pin: current.power_button.led_gpio_pin,
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
  powerButtonSupported.value = data.options.power_button_supported;
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

function togglePowerButton(enabled) {
  config.value.power_button_enabled = enabled;
  confirmReboot.value = false;
}

function onPowerLedPinChange(value) {
  config.value.power_led_gpio_pin = value;
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
    power_button: {
      enabled: config.value.power_button_enabled,
      led_gpio_pin: config.value.power_led_gpio_pin,
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
.hardware-groups {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.hardware-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.hardware-group__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-04);
}

.hardware-description {
  color: var(--color-text-secondary);
}

.hardware-divider {
  height: 1px;
  background: var(--color-border);
}

.encoder-pins {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: var(--space-03);
}

/* Physical rails and fixed pads (IR VCC/GND, the power button's 5V and J2),
   not configurable GPIOs. They are rendered as visually consistent disabled
   dropdowns with the caret removed so they don't suggest a hidden option list. */
.fixed-pin :deep(.dropdown-icon) {
  display: none;
}

/* Sticky apply button (matches MultiroomSettings pattern) */
.apply-button-sticky {
  position: sticky;
  bottom: 0;
  width: 100%;
  z-index: 10;
}

@media (max-aspect-ratio: 4/3) {
  .encoder-pins {
    grid-template-columns: 1fr;
  }
}
</style>
