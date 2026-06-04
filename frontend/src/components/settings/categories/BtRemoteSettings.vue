<!-- frontend/src/components/settings/categories/BtRemoteSettings.vue -->
<template>
  <SettingsContainer>
    <!-- Enabled: connection status, discovery action and volume step.
         The enable/disable toggle lives in the navigation header (SettingsModal). -->
    <SettingsSection v-if="settingsStore.btRemote.enabled">
      <template #header>
        <div class="bt-remote-header">
          <h3 class="bt-remote-header__title heading-3">
            <span class="bt-remote-status">
              <span class="bt-remote-status__dot" :class="{ 'is-connected': btRemoteConnected }" />
              <span class="bt-remote-status__label">{{ btRemoteConnected ? t('btRemoteSettings.connected') : t('btRemoteSettings.notConnected') }}<span
                v-if="btRemoteConnected && settingsStore.btRemote.battery_percentage !== null"
                class="bt-remote-status__battery"
                :class="{ 'is-low': settingsStore.btRemote.battery_percentage < 20 }"
                :title="settingsStore.btRemote.battery_percentage < 20 ? t('btRemoteSettings.batteryLow') : undefined"
              > · {{ settingsStore.btRemote.battery_percentage }}%</span></span>
            </span>
          </h3>
          <Button
            v-if="!btRemoteConnected"
            variant="brand"
            size="small"
            :loading="settingsStore.btRemote.discovering"
            :disabled="settingsStore.btRemote.discovering"
            @click="handleBtRemoteDiscover"
          >
            {{ settingsStore.btRemote.discovering ? t('btRemoteSettings.discovering') : t('btRemoteSettings.discover') }}
          </Button>
          <Button
            v-if="showUnpair"
            class="unpair-button unpair-button--desktop"
            variant="background-strong"
            size="small"
            :loading="unpairing"
            :disabled="unpairing"
            @click="handleUnpair"
          >
            {{ t('btRemoteSettings.unpair') }}
          </Button>
        </div>
      </template>

      <SettingItem :label="t('btRemoteSettings.step')">
        <RangeSlider
          v-model="stepBtRemoteDb"
          :min="1" :max="6" :step="1"
          value-unit=" dB"
          @input="debouncedUpdate('bt-remote-steps', 'bt-remote-steps', { step_bt_remote_db: $event })"
        />
      </SettingItem>

      <Button
        v-if="showUnpair"
        class="unpair-button unpair-button--mobile"
        variant="background-strong"
        size="small"
        :loading="unpairing"
        :disabled="unpairing"
        @click="handleUnpair"
      >
        {{ t('btRemoteSettings.unpair') }}
      </Button>
    </SettingsSection>

    <!-- Disabled: invite the user to enable the feature from the header toggle. -->
    <MessageContent
      v-else
      icon="bluetooth"
      :title="t('btRemoteSettings.disabledTitle')"
      :details="t('btRemoteSettings.disabledDetails')"
    />
  </SettingsContainer>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import Button from '@/components/ui/Button.vue';
import MessageContent from '@/components/ui/MessageContent.vue';

const { t } = useI18n();
const { debouncedUpdate } = useSettingsAPI();
const settingsStore = useSettingsStore();

const stepBtRemoteDb = ref(settingsStore.volumeSteps.step_bt_remote_db);
const unpairing = ref(false);

// Only consider "connected" when not actively discovering (avoids stale state flash)
const btRemoteConnected = computed(() =>
  settingsStore.btRemote.connected && !settingsStore.btRemote.discovering
);

// Hide "Unpair" while a discovery/reconnect is in progress ("Searching..." showing)
const showUnpair = computed(() =>
  settingsStore.btRemote.paired && !settingsStore.btRemote.discovering
);

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
.bt-remote-header {
  display: flex;
  align-items: center;
  gap: var(--space-04);
}

.bt-remote-header__title {
  margin-right: auto;
  min-width: 0;
}

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
}

/* Desktop: unpair lives in the header next to the discover button.
   Narrow/touchscreen: full-width below the slider so the header doesn't crowd. */
.unpair-button--mobile {
  display: none;
}

@media (max-aspect-ratio: 4/3) {
  .unpair-button--desktop {
    display: none;
  }

  .unpair-button--mobile {
    display: flex;
    width: 100%;
    margin-top: var(--space-04);
  }
}
</style>
