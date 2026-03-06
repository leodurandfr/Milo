<!-- frontend/src/components/settings/categories/VolumeSettings.vue -->
<template>
  <SettingsContainer>
    <!-- Volume controls -->
    <SettingsSection :title="t('volumeSettings.controls')">
      <SettingItem :label="t('volumeSettings.rotaryIncrement')">
        <RangeSlider v-model="config.step_rotary_db" :min="1" :max="6" :step="1" value-unit=" dB"
          @input="debouncedUpdate('rotary-steps', 'rotary-steps', { step_rotary_db: $event })" />
      </SettingItem>

      <SettingItem :label="t('volumeSettings.mobileIncrement')">
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
        <span class="bt-remote-status__dot" :class="{ 'is-connected': settingsStore.btRemote.connected }" />
        {{ settingsStore.btRemote.connected ? settingsStore.btRemote.device_name : t('volumeSettings.btRemote.notConnected') }}
        <Button
          v-if="!settingsStore.btRemote.connected"
          variant="brand"
          size="small"
          :loading="discovering"
          @click="handleBtRemoteDiscover"
        >
          {{ discovering ? t('volumeSettings.btRemote.discovering') : t('volumeSettings.btRemote.discover') }}
        </Button>
      </div>

      <SettingItem :label="t('volumeSettings.btRemote.stepLabel')">
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
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import DoubleRangeSlider from '@/components/ui/DoubleRangeSlider.vue';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import ToggleSection from '@/components/settings/ToggleSection.vue';
import Button from '@/components/ui/Button.vue';

const { t } = useI18n();
const { on } = useWebSocket();
const { updateSetting, debouncedUpdate, clearAllTimers } = useSettingsAPI();
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

// BT Remote local UI state (discovering is transient, not in store)
const discovering = ref(false);

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

async function handleBtRemoteDiscover() {
  discovering.value = true;
  await settingsStore.discoverBtRemote();
  discovering.value = false;
}

function handleBtRemoteToggle(enabled) {
  settingsStore.toggleBtRemote(enabled);
}

// WebSocket listeners - update both the store AND local refs
const wsListeners = {
  volume_limits_changed: (msg) => {
    if (msg.data?.limits) {
      const minDb = msg.data.limits.min_db ?? -80.0;
      const maxDb = msg.data.limits.max_db ?? -21.0;
      settingsStore.updateVolumeLimits({ min_db: minDb, max_db: maxDb });
      config.value.limits.min = minDb;
      config.value.limits.max = maxDb;
    }
  },
  volume_startup_changed: (msg) => {
    if (msg.data?.config) {
      const startupDb = msg.data.config.startup_volume_db;
      settingsStore.updateVolumeStartup({
        restore_last_volume: msg.data.config.restore_last_volume,
        startup_volume_db: startupDb
      });
      config.value.restore_last_volume = msg.data.config.restore_last_volume;
      config.value.startup_volume_db = startupDb;
    }
  },
  volume_steps_changed: (msg) => {
    // step_mobile_db is handled by unifiedAudioStore via volume:volume_changed event
    // Just update local config for immediate UI responsiveness
    if (msg.data?.config?.step_mobile_db !== undefined) {
      config.value.step_mobile_db = msg.data.config.step_mobile_db;
    }
  },
  rotary_steps_changed: (msg) => {
    if (msg.data?.config?.step_rotary_db !== undefined) {
      const stepDb = msg.data.config.step_rotary_db;
      settingsStore.updateVolumeSteps({ step_rotary_db: stepDb });
      config.value.step_rotary_db = stepDb;
    }
  },
  bt_remote_steps_changed: (msg) => {
    if (msg.data?.config?.step_bt_remote_db !== undefined) {
      const stepDb = msg.data.config.step_bt_remote_db;
      settingsStore.updateVolumeSteps({ step_bt_remote_db: stepDb });
      config.value.step_bt_remote_db = stepDb;
    }
  }
};

onMounted(() => {
  // Sync with the store on mount
  syncFromStore();

  // Register WebSocket listeners
  Object.entries(wsListeners).forEach(([eventType, handler]) => {
    on('settings', eventType, handler);
  });
});

onUnmounted(() => {
  clearAllTimers();
});
</script>

<style scoped>
.bt-remote-status {
  color: var(--color-text-secondary);
  margin-bottom: var(--space-05);
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
}

.bt-remote-status__dot.is-connected {
  background: var(--color-success);
}

.bt-remote-status :deep(.btn) {
  margin-left: auto;
}

</style>
