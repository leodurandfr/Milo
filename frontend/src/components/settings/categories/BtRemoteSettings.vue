<!-- frontend/src/components/settings/categories/BtRemoteSettings.vue -->
<template>
  <SettingsContainer>
    <ToggleSection
      heading="3"
      :enabled="settingsStore.btRemote.enabled"
      @change="handleBtRemoteToggle"
    >
      <template #title>
        <span class="bt-remote-status">
          <span class="bt-remote-status__dot" :class="{ 'is-connected': btRemoteConnected }" />
          <span class="bt-remote-status__label">{{ btRemoteConnected ? t('btRemoteSettings.connected') : t('btRemoteSettings.notConnected') }}<span
            v-if="btRemoteConnected && settingsStore.btRemote.battery_percentage !== null"
            class="bt-remote-status__battery"
            :class="{ 'is-low': settingsStore.btRemote.battery_percentage < 20 }"
            :title="settingsStore.btRemote.battery_percentage < 20 ? t('btRemoteSettings.batteryLow') : undefined"
          > · {{ settingsStore.btRemote.battery_percentage }}%</span></span>
        </span>
      </template>
      <template v-if="!btRemoteConnected" #actions>
        <Button
          variant="brand"
          size="small"
          :loading="settingsStore.btRemote.discovering"
          :disabled="settingsStore.btRemote.discovering"
          @click="handleBtRemoteDiscover"
        >
          {{ settingsStore.btRemote.discovering ? t('btRemoteSettings.discovering') : t('btRemoteSettings.discover') }}
        </Button>
      </template>

      <SettingItem :label="t('btRemoteSettings.step')">
        <RangeSlider
          v-model="stepBtRemoteDb"
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
import { useSettingsStore } from '@/stores/settingsStore';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import ToggleSection from '@/components/ui/ToggleSection.vue';
import Button from '@/components/ui/Button.vue';

const { t } = useI18n();
const { debouncedUpdate, clearAllTimers } = useSettingsAPI();
const settingsStore = useSettingsStore();

const stepBtRemoteDb = ref(settingsStore.volumeSteps.step_bt_remote_db);

// Only consider "connected" when not actively discovering (avoids stale state flash)
const btRemoteConnected = computed(() =>
  settingsStore.btRemote.connected && !settingsStore.btRemote.discovering
);

function handleBtRemoteDiscover() {
  settingsStore.discoverBtRemote();
}

function handleBtRemoteToggle(enabled) {
  settingsStore.toggleBtRemote(enabled);
}

// Sync local slider with the store (e.g., WS event from another device)
watch(
  () => settingsStore.volumeSteps.step_bt_remote_db,
  (value) => { stepBtRemoteDb.value = value; }
);

onMounted(() => {
  settingsStore.loadBtRemoteStatus();
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
.bt-remote-status {
  display: inline-flex;
  align-items: center;
  gap: var(--space-02);
  vertical-align: top;
}

.bt-remote-status__dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-error);
}

.bt-remote-status__dot.is-connected {
  background: var(--color-success);
}

.bt-remote-status__battery.is-low {
  color: var(--color-warning);
  font-weight: 600;
}
</style>
