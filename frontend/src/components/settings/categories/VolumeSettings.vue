<!-- frontend/src/components/settings/categories/VolumeSettings.vue -->
<template>
  <!-- DAC mode: volume not managed by Milō on any device -->
  <div v-if="!unifiedStore.volumeState.any_volume_control" class="dac-notice">
    <span class="text-mono">{{ t('volumeSettings.volumeNotManaged') }}</span>
  </div>

  <SettingsContainer v-else>
    <!-- Volume controls -->
    <SettingsSection :title="t('volumeSettings.controls')">
      <SettingItem v-if="rotaryEnabled" :label="t('volumeSettings.rotaryStep')">
        <RangeSlider v-model="config.step_rotary_db" :min="1" :max="6" :step="1" value-unit=" dB"
          @input="debouncedUpdate('rotary-steps', 'rotary-steps', { step_rotary_db: $event })" />
      </SettingItem>

      <SettingItem :label="t('volumeSettings.mobileStep')">
        <RangeSlider v-model="config.step_mobile_db" :min="1" :max="6" :step="1" value-unit=" dB"
          @input="debouncedUpdate('volume-steps', 'volume-steps', { step_mobile_db: $event })" />
      </SettingItem>
    </SettingsSection>

    <!-- Volume limits -->
    <SettingsSection :title="t('volumeSettings.limits')">
      <SettingItem :label="t('volumeSettings.minMax')">
        <DoubleRangeSlider v-model="config.limits" :min="-80" :max="0" :step="1" :gap="6" value-unit=" dB"
          @input="updateVolumeLimits" />
      </SettingItem>
    </SettingsSection>

    <!-- Startup volume -->
    <SettingsSection :title="t('volumeSettings.startup')">
      <ButtonGroup
        :model-value="config.restore_last_volume"
        :options="startupModeOptions"
        mobile-layout="column-reverse"
        @change="handleStartupModeChange"
      />

      <SettingItem v-if="!config.restore_last_volume" :label="t('volumeSettings.fixedStartup')">
        <RangeSlider v-model="config.startup_volume_db" :min="config.limits.min" :max="config.limits.max" :step="1" value-unit=" dB"
          @input="debouncedUpdate('volume-startup', 'volume-startup', { startup_volume_db: $event, restore_last_volume: false })" />
      </SettingItem>
    </SettingsSection>

    <!-- Bluetooth Remote (ANTICATER VK1 Mini) -->
    <ToggleSection
      :title="t('volumeSettings.btRemote.title')"
      :enabled="settingsStore.btRemote.enabled"
      @change="handleBtRemoteToggle"
    >
      <div class="bt-remote-status text-mono">
        <span class="bt-remote-status__dot" :class="{ 'is-connected': btRemoteConnected }" />
        {{ btRemoteConnected ? t('volumeSettings.btRemote.connected') : t('volumeSettings.btRemote.notConnected') }}
        <span v-if="btRemoteConnected && settingsStore.btRemote.battery_percentage !== null"
          class="bt-remote-status__battery"
          :class="{ 'is-low': settingsStore.btRemote.battery_percentage < 20 }"
          :title="settingsStore.btRemote.battery_percentage < 20 ? t('volumeSettings.btRemote.batteryLow') : undefined">
          — {{ settingsStore.btRemote.battery_percentage }}%
        </span>
        <Button
          v-if="!btRemoteConnected"
          variant="brand"
          size="small"
          :loading="settingsStore.btRemote.discovering"
          :disabled="settingsStore.btRemote.discovering"
          @click="handleBtRemoteDiscover"
        >
          {{ settingsStore.btRemote.discovering ? t('volumeSettings.btRemote.discovering') : t('volumeSettings.btRemote.discover') }}
        </Button>
      </div>

      <SettingItem :label="t('volumeSettings.rotaryStep')">
        <RangeSlider
          v-model="config.step_bt_remote_db"
          :min="1" :max="6" :step="1"
          value-unit=" dB"
          @input="debouncedUpdate('bt-remote-steps', 'bt-remote-steps', { step_bt_remote_db: $event })"
        />
      </SettingItem>
    </ToggleSection>
  </SettingsContainer>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useHardwareConfig } from '@/composables/useHardwareConfig';
import { useSettingsStore } from '@/stores/settingsStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import DoubleRangeSlider from '@/components/ui/DoubleRangeSlider.vue';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import ToggleSection from '@/components/ui/ToggleSection.vue';
import Button from '@/components/ui/Button.vue';

const { t } = useI18n();
const { updateSetting, debouncedUpdate, clearAllTimers } = useSettingsAPI();
const { rotaryEnabled } = useHardwareConfig();
const settingsStore = useSettingsStore();
const unifiedStore = useUnifiedAudioStore();

// Local refs for instant responsiveness (all values in dB)
const config = ref({
  step_mobile_db: 3.0,
  step_rotary_db: 2.0,
  step_bt_remote_db: 2.0,
  limits: { min: -80.0, max: -21.0 },
  restore_last_volume: false,
  startup_volume_db: -60.0
});

// Only consider "connected" when not actively discovering (avoids stale state flash)
const btRemoteConnected = computed(() =>
  settingsStore.btRemote.connected && !settingsStore.btRemote.discovering
);

// Startup mode options for ButtonGroup
const startupModeOptions = computed(() => [
  { label: t('volumeSettings.fixedVolume'), value: false },
  { label: t('volumeSettings.restoreLast'), value: true }
]);

function handleStartupModeChange(restoreLast) {
  updateSetting('volume-startup', {
    startup_volume_db: config.value.startup_volume_db,
    restore_last_volume: restoreLast
  });
}

// Sync local refs with the stores on mount
function syncFromStore() {
  // step_mobile_db comes from unifiedAudioStore (single source of truth)
  config.value.step_mobile_db = unifiedStore.volumeState.step_mobile_db;
  config.value.step_rotary_db = settingsStore.volumeSteps.step_rotary_db;
  config.value.step_bt_remote_db = settingsStore.volumeSteps.step_bt_remote_db;
  config.value.limits.min = settingsStore.volumeLimits.min_db;
  config.value.limits.max = settingsStore.volumeLimits.max_db;
  config.value.restore_last_volume = settingsStore.volumeStartup.restore_last_volume;
  config.value.startup_volume_db = settingsStore.volumeStartup.startup_volume_db;
}

function updateVolumeLimits(limits) {
  debouncedUpdate('volume-limits', 'volume-limits', {
    min_db: limits.min,
    max_db: limits.max
  });
}

// === BT Remote functions ===

function handleBtRemoteDiscover() {
  settingsStore.discoverBtRemote();
}

function handleBtRemoteToggle(enabled) {
  settingsStore.toggleBtRemote(enabled);
}

// Sync local config when store changes (e.g., WS event from another device)
watch(
  [
    () => settingsStore.volumeLimits,
    () => settingsStore.volumeStartup,
    () => settingsStore.volumeSteps,
    () => unifiedStore.volumeState.step_mobile_db
  ],
  syncFromStore,
  { deep: true }
);

onMounted(() => {
  syncFromStore();
  // Fetch battery on-demand (only when this settings page is open)
  if (settingsStore.btRemote.connected) {
    settingsStore.fetchBtRemoteBattery();
  }
});

onUnmounted(() => {
  clearAllTimers();
});
</script>

<style scoped>
.dac-notice {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-06) var(--space-04);
  color: var(--color-text-secondary);
  text-align: center;
}

.bt-remote-status {
  color: var(--color-text-secondary);
  margin-bottom: var(--space-04);
  display: flex;
  align-items: center;
  gap: var(--space-02);
}

.bt-remote-status__dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  vertical-align: middle;
  background: var(--color-error);
}

.bt-remote-status__dot.is-connected {
  background: var(--color-success);
}

.bt-remote-status__battery.is-low {
  color: var(--color-warning);
  font-weight: 600;
}

.bt-remote-status :deep(.btn) {
  margin-left: auto;
}

</style>
