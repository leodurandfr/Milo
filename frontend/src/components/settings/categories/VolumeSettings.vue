<!-- frontend/src/components/settings/categories/VolumeSettings.vue -->
<template>
  <!-- DAC mode: volume not managed by Milō on any device -->
  <div v-if="!unifiedStore.volumeState.any_volume_control" class="dac-notice">
    <span class="text-mono-medium">{{ t('volumeSettings.volumeNotManaged') }}</span>
  </div>

  <SectionStack v-else>
    <!-- Volume controls -->
    <SectionCard :title="t('volumeSettings.controls')">
      <RangeSlider v-if="rotaryEnabled" :label="t('volumeSettings.rotaryStep')" v-model="config.step_rotary_db" :min="1" :max="6" :step="1" ticks unit="dB"
        @change="updateSetting('rotary-steps', { step_rotary_db: $event })" />

      <RangeSlider :label="t('volumeSettings.mobileStep')" v-model="config.step_mobile_db" :min="1" :max="6" :step="1" ticks unit="dB"
        @change="updateSetting('volume-steps', { step_mobile_db: $event })" />
    </SectionCard>

    <!-- Volume limits -->
    <SectionCard :title="t('volumeSettings.limits')">
      <DoubleRangeSlider :label="t('volumeSettings.minMax')" v-model="config.limits" :min="-80" :max="0" :step="1" :gap="6" unit="dB"
        @change="updateVolumeLimits" />
    </SectionCard>

    <!-- Startup volume -->
    <SectionCard :title="t('volumeSettings.startup')">
      <ButtonGroup
        :model-value="config.restore_last_volume"
        :options="startupModeOptions"
        mobile-layout="column-reverse"
        @change="handleStartupModeChange"
      />

      <Collapse :open="!config.restore_last_volume">
        <RangeSlider :label="t('volumeSettings.fixedStartup')" v-model="config.startup_volume_db" :min="config.limits.min" :max="config.limits.max" :step="1" unit="dB"
          @change="updateSetting('volume-startup', { startup_volume_db: $event, restore_last_volume: false })" />
      </Collapse>
    </SectionCard>
  </SectionStack>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useHardwareConfig } from '@/composables/useHardwareConfig';
import { useSettingsStore } from '@/stores/settingsStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import Collapse from '@/components/ui/Collapse.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import DoubleRangeSlider from '@/components/ui/DoubleRangeSlider.vue';
import SectionStack from '@/components/ui/SectionStack.vue';
import SectionCard from '@/components/ui/SectionCard.vue';

const { t } = useI18n();
const { updateSetting } = useSettingsAPI();
const { rotaryEnabled } = useHardwareConfig();
const settingsStore = useSettingsStore();
const unifiedStore = useUnifiedAudioStore();

// Local refs for instant responsiveness (all values in dB), taken from the
// store — never from placeholders a first frame would show.
const config = ref(fromStore());

const startupModeOptions = computed(() => [
  { label: t('volumeSettings.fixedVolume'), value: false },
  { label: t('volumeSettings.restoreLast'), value: true }
]);

function handleStartupModeChange(restoreLast) {
  if (restoreLast === config.value.restore_last_volume) return;
  // Flipped here, not on the store's round trip: the slider moves on the tap.
  config.value.restore_last_volume = restoreLast;
  updateSetting('volume-startup', {
    startup_volume_db: config.value.startup_volume_db,
    restore_last_volume: restoreLast
  }).catch(syncFromStore);
}

function fromStore() {
  return {
    step_mobile_db: settingsStore.volumeSteps.step_mobile_db,
    step_rotary_db: settingsStore.volumeSteps.step_rotary_db,
    limits: { min: settingsStore.volumeLimits.min_db, max: settingsStore.volumeLimits.max_db },
    restore_last_volume: settingsStore.volumeStartup.restore_last_volume,
    startup_volume_db: Math.round(settingsStore.volumeStartup.startup_volume_db),
  };
}

function syncFromStore() {
  config.value = fromStore();
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
    () => settingsStore.volumeSteps
  ],
  syncFromStore,
  { deep: true }
);

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
