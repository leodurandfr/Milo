<!-- frontend/src/components/settings/categories/AudioPlaybackSettings.vue -->
<template>
  <SettingsContainer>
    <!-- Auto-disconnect on pause (applies to every eligible source) -->
    <ToggleSection
      :title="t('audioPlayback.autoDisconnect')"
      :enabled="autoDisconnectEnabled"
      @change="handleAutoDisconnectToggle"
    >
      <SettingItem :label="t('audioPlayback.autoDisconnectHint')">
        <ButtonGroup
          :model-value="config.auto_disconnect_delay"
          :options="autoDisconnectPresets"
          mobile-layout="grid-3"
          @change="setAutoDisconnectDelay"
        />
      </SettingItem>
      <p class="audio-playback__note text-mono">{{ t('audioPlayback.notApplicableNote') }}</p>
    </ToggleSection>

    <!-- Inactivity timeout (deactivate audio source after a long idle period) -->
    <ToggleSection
      :title="t('audioPlayback.inactivityTimeout')"
      :enabled="inactivityEnabled"
      @change="handleInactivityToggle"
    >
      <SettingItem :label="t('audioPlayback.inactivityHint')">
        <ButtonGroup
          :model-value="config.inactivity_timeout"
          :options="inactivityPresets"
          mobile-layout="grid-3"
          @change="setInactivityTimeout"
        />
      </SettingItem>
    </ToggleSection>
  </SettingsContainer>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import ToggleSection from '@/components/ui/ToggleSection.vue';

const { t } = useI18n();
const { updateSetting } = useSettingsAPI();
const settingsStore = useSettingsStore();

// Local state for instant responsiveness
const config = ref({
  auto_disconnect_delay: 120,
  inactivity_timeout: 7200
});

// Remember last non-zero values so toggling OFF then ON restores the user choice
const lastAutoDisconnect = ref(120);
const lastInactivity = ref(7200);

const autoDisconnectEnabled = computed(() => config.value.auto_disconnect_delay !== 0);
const inactivityEnabled = computed(() => config.value.inactivity_timeout !== 0);

const autoDisconnectPresets = computed(() => [
  { value: 30, label: t('time.30sec') },
  { value: 120, label: t('time.2min') },
  { value: 300, label: t('time.5min') },
  { value: 600, label: t('time.10min') },
  { value: 1800, label: t('time.30min') }
]);

const inactivityPresets = computed(() => [
  { value: 300, label: t('time.5min') },
  { value: 1800, label: t('time.30min') },
  { value: 3600, label: t('time.1h') },
  { value: 7200, label: t('time.2h') },
  { value: 21600, label: t('time.6h') },
  { value: 43200, label: t('time.12h') }
]);

function syncFromStore() {
  const playback = settingsStore.audioPlayback;
  config.value.auto_disconnect_delay = playback.auto_disconnect_delay;
  config.value.inactivity_timeout = playback.inactivity_timeout;
  if (playback.auto_disconnect_delay > 0) {
    lastAutoDisconnect.value = playback.auto_disconnect_delay;
  }
  if (playback.inactivity_timeout > 0) {
    lastInactivity.value = playback.inactivity_timeout;
  }
}

function handleAutoDisconnectToggle(enabled) {
  const next = enabled ? lastAutoDisconnect.value : 0;
  if (!enabled && config.value.auto_disconnect_delay > 0) {
    lastAutoDisconnect.value = config.value.auto_disconnect_delay;
  }
  config.value.auto_disconnect_delay = next;
  settingsStore.updateAudioPlayback({ auto_disconnect_delay: next });
  updateSetting('audio-disconnect', { auto_disconnect_delay: next });
}

function setAutoDisconnectDelay(value) {
  if (value > 0) {
    lastAutoDisconnect.value = value;
  }
  config.value.auto_disconnect_delay = value;
  settingsStore.updateAudioPlayback({ auto_disconnect_delay: value });
  updateSetting('audio-disconnect', { auto_disconnect_delay: value });
}

function handleInactivityToggle(enabled) {
  const next = enabled ? lastInactivity.value : 0;
  if (!enabled && config.value.inactivity_timeout > 0) {
    lastInactivity.value = config.value.inactivity_timeout;
  }
  config.value.inactivity_timeout = next;
  settingsStore.updateAudioPlayback({ inactivity_timeout: next });
  updateSetting('inactivity-timeout', { inactivity_timeout: next });
}

function setInactivityTimeout(value) {
  if (value > 0) {
    lastInactivity.value = value;
  }
  config.value.inactivity_timeout = value;
  settingsStore.updateAudioPlayback({ inactivity_timeout: value });
  updateSetting('inactivity-timeout', { inactivity_timeout: value });
}

// Re-sync when the store changes (e.g. WS event from another device)
watch(() => settingsStore.audioPlayback, syncFromStore, { deep: true });

onMounted(() => {
  syncFromStore();
});
</script>

<style scoped>
.audio-playback__note {
  color: var(--color-text-secondary);
  margin: 0;
}
</style>
