<!-- frontend/src/components/settings/categories/BtRemoteSettings.vue -->
<template>
  <SettingsContainer>
    <!-- Disabled: invite the user to enable the feature from the header toggle. -->
    <MessageContent
      v-if="!settingsStore.btRemote.enabled"
      icon="bluetooth"
      :title="t('btRemoteSettings.disabledTitle')"
      :details="t('btRemoteSettings.disabledDetails')"
    />

    <!-- Enabled: status card. Red dot + "Search" while disconnected, green dot +
         "Unpair" once connected. Volume step stays editable in both states. -->
    <RemoteStatusSection
      v-else
      v-model="stepBtRemoteDb"
      :ok="btRemoteConnected"
      :cta-label="ctaLabel"
      :cta-loading="settingsStore.btRemote.discovering"
      :cta-disabled="settingsStore.btRemote.discovering"
      :cta-click="handleBtRemoteDiscover"
      :step-label="t('btRemoteSettings.step')"
      :show-unpair="btRemoteConnected"
      :unpair-label="t('btRemoteSettings.unpair')"
      :unpair-loading="unpairing"
      :unpair-click="handleUnpair"
      @step-input="debouncedUpdate('bt-remote-steps', 'bt-remote-steps', { step_bt_remote_db: $event })"
    >
      <template #status>{{ btRemoteConnected ? t('btRemoteSettings.connected') : t('btRemoteSettings.notConnected') }}<span
        v-if="btRemoteConnected && settingsStore.btRemote.battery_percentage !== null"
        class="bt-remote-battery"
        :class="{ 'is-low': settingsStore.btRemote.battery_percentage < 20 }"
        :title="settingsStore.btRemote.battery_percentage < 20 ? t('btRemoteSettings.batteryLow') : undefined"
      > · {{ settingsStore.btRemote.battery_percentage }}%</span></template>
    </RemoteStatusSection>
  </SettingsContainer>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import RemoteStatusSection from '@/components/settings/categories/RemoteStatusSection.vue';

const { t } = useI18n();
const { debouncedUpdate } = useSettingsAPI();
const settingsStore = useSettingsStore();

const stepBtRemoteDb = ref(settingsStore.volumeSteps.step_bt_remote_db);
const unpairing = ref(false);

// Only consider "connected" when not actively discovering (avoids stale state flash)
const btRemoteConnected = computed(() =>
  settingsStore.btRemote.connected && !settingsStore.btRemote.discovering
);

// Scan/reconnect CTA while disconnected; once connected only unpair remains.
const ctaLabel = computed(() => {
  if (btRemoteConnected.value) return null;
  return settingsStore.btRemote.discovering
    ? t('btRemoteSettings.discovering')
    : t('btRemoteSettings.discover');
});

function handleBtRemoteDiscover() {
  settingsStore.discoverBtRemote();
}

async function handleUnpair() {
  unpairing.value = true;
  try {
    await settingsStore.unpairBtRemote();
  } finally {
    unpairing.value = false;
  }
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
</script>

<style scoped>
.bt-remote-battery.is-low {
  color: var(--color-warning);
}
</style>
