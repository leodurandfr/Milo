<!-- frontend/src/components/settings/categories/RemoteControlsSettings.vue -->
<template>
  <SettingsContainer>
    <SettingsSection>
      <div class="remote-list">
        <ListItemButton
          variant="background"
          action="caret"
          @click="$emit('open-bt-remote')"
        >
          <template #title>
            <div class="remote-title">
              <span>{{ t('remoteControls.btRemote') }}</span>
              <span class="remote-title__meta text-mono-small">
                <span class="remote-title__dot" :class="btDotClass" />
                {{ btStatusText }}
              </span>
            </div>
          </template>
        </ListItemButton>

        <ListItemButton
          variant="background"
          action="caret"
          @click="$emit('open-ir-remote')"
        >
          <template #title>
            <div class="remote-title">
              <span>{{ t('remoteControls.irRemote') }}</span>
              <span class="remote-title__meta text-mono-small">
                <span class="remote-title__dot" :class="irDotClass" />
                {{ irStatusText }}
              </span>
            </div>
          </template>
        </ListItemButton>
      </div>
    </SettingsSection>
  </SettingsContainer>
</template>

<script setup>
import { computed, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSettingsStore } from '@/stores/settingsStore';
import { useHardwareConfig } from '@/composables/useHardwareConfig';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';

defineEmits(['open-bt-remote', 'open-ir-remote']);

const { t } = useI18n();
const settingsStore = useSettingsStore();
const { hardwareConfig } = useHardwareConfig();

const irHardwareEnabled = computed(
  () => hardwareConfig.value?.current?.ir_remote?.enabled !== false
);

const btStatusText = computed(() => {
  const bt = settingsStore.btRemote;
  if (!bt.enabled) return t('remoteControls.status.disabled');
  if (bt.connected) return t('remoteControls.status.connected');
  if (bt.discovering) return t('btRemoteSettings.discovering');
  return t('btRemoteSettings.notConnected');
});

const btDotClass = computed(() => {
  const bt = settingsStore.btRemote;
  if (!bt.enabled) return 'remote-title__dot--off';
  if (bt.connected) return 'remote-title__dot--ok';
  return 'remote-title__dot--idle';
});

const irStatusText = computed(() => {
  if (!irHardwareEnabled.value) return t('remoteControls.status.hardwareDisabled');
  if (!settingsStore.irRemote.paired) return t('remoteControls.status.notPaired');
  return t('remoteControls.status.paired');
});

const irDotClass = computed(() => {
  if (!irHardwareEnabled.value || !settingsStore.irRemote.paired) return 'remote-title__dot--off';
  return 'remote-title__dot--ok';
});

onMounted(() => {
  settingsStore.loadBtRemoteStatus();
  settingsStore.loadIrRemoteStatus();
});
</script>

<style scoped>
.remote-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.remote-title {
  display: flex;
  flex-direction: column;
  gap: 2px;
  text-align: left;
}

.remote-title__meta {
  display: inline-flex;
  align-items: center;
  gap: var(--space-01);
  color: var(--color-text-secondary);
}

.remote-title__dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.remote-title__dot--off {
  background: var(--color-background-medium-16);
}

.remote-title__dot--idle {
  background: var(--color-error);
}

.remote-title__dot--ok {
  background: var(--color-success);
}
</style>
