<!-- frontend/src/components/settings/categories/VolumeSettings.vue -->
<template>
  <!-- DAC mode: volume not managed by Milō on any device -->
  <MessageContent v-if="!unifiedStore.volumeState.any_volume_control" icon="volume"
    :title="t('volumeSettings.volumeNotManaged')" />

  <SectionStack v-else>
    <!-- Volume controls -->
    <SectionCard :title="t('volumeSettings.controls')">
      <RangeSlider v-if="rotaryEnabled" :label="t('volumeSettings.rotaryStep')" v-model="config.step_rotary_db" :min="1" :max="6" :step="1" ticks unit="dB"
        @change="updateSetting('rotary-steps', { step_rotary_db: $event })" />

      <RangeSlider :label="t('volumeSettings.buttonsStep')" v-model="config.step_interface_db" :min="1" :max="6" :step="1" ticks unit="dB"
        @change="updateSetting('volume-steps', { step_interface_db: $event })" />
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

    <!-- On which screens the dock offers the volume control. Dock's own Volume
         row leads here, since that row has no switch. -->
    <SectionCard :title="t('volumeSettings.inDock')" :description="t('volumeSettings.inDockDescription')">
      <div class="surface-list">
        <ListItemButton
          v-for="surface in DOCK_SURFACES"
          :key="surface.key"
          :title="t(surface.labelKey)"
          action="toggle"
          :model-value="settingsStore.dockVolume[surface.key]"
          @update:model-value="(value) => handleDockVolumeToggle(surface.key, value)"
        />
      </div>
    </SectionCard>

    <SettingsRefusal :detail="refusal" />
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
import MessageContent from '@/components/ui/MessageContent.vue';
import SettingsRefusal from '@/components/settings/SettingsRefusal.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';

const { t } = useI18n();
// Every control here holds a local copy, which a refused write puts back on
// the store.
const { updateSetting, debouncedUpdate, refusal } = useSettingsAPI({ resync: syncFromStore });
const { rotaryEnabled } = useHardwareConfig();
const settingsStore = useSettingsStore();

const DOCK_SURFACES = [
  { key: 'milo_screen', labelKey: 'dockVolume.miloScreen' },
  { key: 'desktop_browser', labelKey: 'dockVolume.desktopBrowser' },
  { key: 'phone_browser', labelKey: 'dockVolume.phoneBrowser' },
  { key: 'ios_app', labelKey: 'dockVolume.iosApp' }
];

// The switches draw the store, which a refused write reads back.
function handleDockVolumeToggle(surface, value) {
  settingsStore.updateDockVolume({ [surface]: value });
  debouncedUpdate('dock-volume', 'dock-volume', { ...settingsStore.dockVolume }, 300);
}
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
  });
}

function fromStore() {
  return {
    step_interface_db: settingsStore.volumeSteps.step_interface_db,
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
.surface-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-01);
}

@media (max-aspect-ratio: 4/3) and (max-width: 1023px) {
  .surface-list {
    display: flex;
    flex-direction: column;
  }
}
</style>
