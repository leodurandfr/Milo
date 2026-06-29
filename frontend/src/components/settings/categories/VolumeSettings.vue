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
          @change="updateSetting('rotary-steps', { step_rotary_db: $event })" />
      </SettingItem>

      <SettingItem :label="t('volumeSettings.mobileStep')">
        <RangeSlider v-model="config.step_mobile_db" :min="1" :max="6" :step="1" value-unit=" dB"
          @change="updateSetting('volume-steps', { step_mobile_db: $event })" />
      </SettingItem>
    </SettingsSection>

    <!-- Volume limits -->
    <SettingsSection :title="t('volumeSettings.limits')">
      <SettingItem :label="t('volumeSettings.minMax')">
        <DoubleRangeSlider v-model="config.limits" :min="-80" :max="0" :step="1" :gap="6" value-unit=" dB"
          @change="updateVolumeLimits" />
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
          @change="updateSetting('volume-startup', { startup_volume_db: $event, restore_last_volume: false })" />
      </SettingItem>
    </SettingsSection>
  </SettingsContainer>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue';
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

const { t } = useI18n();
const { updateSetting } = useSettingsAPI();
const { rotaryEnabled } = useHardwareConfig();
const settingsStore = useSettingsStore();
const unifiedStore = useUnifiedAudioStore();

// Local refs for instant responsiveness (all values in dB)
const config = ref({
  step_mobile_db: 3.0,
  step_rotary_db: 2.0,
  limits: { min: -80.0, max: -21.0 },
  restore_last_volume: false,
  startup_volume_db: -60.0
});

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
  config.value.limits.min = settingsStore.volumeLimits.min_db;
  config.value.limits.max = settingsStore.volumeLimits.max_db;
  config.value.restore_last_volume = settingsStore.volumeStartup.restore_last_volume;
  config.value.startup_volume_db = Math.round(settingsStore.volumeStartup.startup_volume_db);
}

function updateVolumeLimits(limits) {
  updateSetting('volume-limits', {
    min_db: limits.min,
    max_db: limits.max
  });
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

onMounted(syncFromStore);
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
</style>
