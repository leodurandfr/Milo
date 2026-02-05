<!-- frontend/src/components/settings/categories/SpotifySettings.vue -->
<template>
  <ToggleSection
    :title="t('spotifySettings.autoDisconnect')"
    :enabled="config.auto_disconnect_enabled"
    @change="handleAutoDisconnectToggle"
  >
    <SettingItem :label="t('spotifySettings.disconnectDelay')">
      <ButtonGroup
        :model-value="config.auto_disconnect_delay"
        :options="delayPresets"
        mobile-layout="grid-3"
        @change="setSpotifyDisconnect"
      />
    </SettingItem>
  </ToggleSection>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import ToggleSection from '@/components/settings/ToggleSection.vue';

const { t } = useI18n();
const { on } = useWebSocket();
const { updateSetting } = useSettingsAPI();
const settingsStore = useSettingsStore();

// Local state for instant responsiveness
const config = ref({
  auto_disconnect_enabled: true,
  auto_disconnect_delay: 120
});

// Remembers last non-zero delay for restore on toggle ON
const lastNonZeroDelay = ref(120);

// Sync local refs with the store on mount
function syncFromStore() {
  const delay = settingsStore.spotifyDisconnect.auto_disconnect_delay;
  config.value.auto_disconnect_delay = delay;
  config.value.auto_disconnect_enabled = delay !== 0;

  if (delay > 0) {
    lastNonZeroDelay.value = delay;
  }
}

const delayPresets = computed(() => [
  { value: 10, label: t('time.10sec') },
  { value: 30, label: t('time.30sec') },
  { value: 120, label: t('time.2min') },
  { value: 300, label: t('time.5min') },
  { value: 600, label: t('time.10min') },
  { value: 1800, label: t('time.30min') }
]);

function handleAutoDisconnectToggle(enabled) {
  if (enabled) {
    config.value.auto_disconnect_enabled = true;
    config.value.auto_disconnect_delay = lastNonZeroDelay.value;
    updateSetting('spotify-disconnect', { auto_disconnect_delay: lastNonZeroDelay.value });
  } else {
    if (config.value.auto_disconnect_delay > 0) {
      lastNonZeroDelay.value = config.value.auto_disconnect_delay;
    }
    config.value.auto_disconnect_enabled = false;
    config.value.auto_disconnect_delay = 0;
    updateSetting('spotify-disconnect', { auto_disconnect_delay: 0 });
  }
}

function setSpotifyDisconnect(value) {
  if (value > 0) {
    lastNonZeroDelay.value = value;
  }
  config.value.auto_disconnect_delay = value;
  updateSetting('spotify-disconnect', { auto_disconnect_delay: value });
}

// WebSocket listener - updates both store and local state
const handleSpotifyDisconnectChanged = (msg) => {
  if (msg.data?.config?.auto_disconnect_delay !== undefined) {
    const delay = msg.data.config.auto_disconnect_delay;
    settingsStore.updateSpotifyDisconnect({ auto_disconnect_delay: delay });
    config.value.auto_disconnect_delay = delay;
    config.value.auto_disconnect_enabled = delay !== 0;

    if (delay > 0) {
      lastNonZeroDelay.value = delay;
    }
  }
};

onMounted(() => {
  syncFromStore();
  on('settings', 'spotify_disconnect_changed', handleSpotifyDisconnectChanged);
});
</script>
