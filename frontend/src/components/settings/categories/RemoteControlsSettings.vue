<!-- frontend/src/components/settings/categories/RemoteControlsSettings.vue -->
<template>
  <SettingsContainer>
    <SettingsSection>
      <div class="remote-list">
        <ListItemButton
          variant="background"
          icon-variant="standard"
          action="caret"
          :title="t('remoteControls.btRemote')"
          @click="$emit('open-bt-remote')"
        >
          <template #icon>
            <SvgIcon name="bluetooth" :size="28" />
          </template>
          <template #subtitle>
            <span class="remote-meta text-mono-small">
              <span class="remote-dot" :class="btDotClass" />
              {{ btStatusText }}
            </span>
          </template>
        </ListItemButton>

        <ListItemButton
          variant="background"
          icon-variant="standard"
          action="caret"
          :title="t('remoteControls.irRemote')"
          @click="$emit('open-ir-remote')"
        >
          <template #icon>
            <SvgIcon name="infrared" :size="28" />
          </template>
          <template #subtitle>
            <span class="remote-meta text-mono-small">
              <span class="remote-dot" :class="irDotClass" />
              {{ irStatusText }}
            </span>
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
import SvgIcon from '@/components/ui/SvgIcon.vue';

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
  if (!bt.enabled) return 'remote-dot--off';
  if (bt.connected) return 'remote-dot--ok';
  return 'remote-dot--idle';
});

const irStatusText = computed(() => {
  if (!irHardwareEnabled.value) return t('remoteControls.status.hardwareDisabled');
  if (!settingsStore.irRemote.enabled) return t('remoteControls.status.disabled');
  if (!settingsStore.irRemote.paired) return t('remoteControls.status.notPaired');
  return t('remoteControls.status.paired');
});

const irDotClass = computed(() => {
  if (!irHardwareEnabled.value) return 'remote-dot--off';
  if (!settingsStore.irRemote.enabled) return 'remote-dot--off';
  if (!settingsStore.irRemote.paired) return 'remote-dot--idle';
  return 'remote-dot--ok';
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

.remote-meta {
  display: inline-flex;
  align-items: center;
  gap: var(--space-01);
  color: var(--color-text-secondary);
}

.remote-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.remote-dot--off {
  background: var(--color-background-medium-16);
}

.remote-dot--idle {
  background: var(--color-error);
}

.remote-dot--ok {
  background: var(--color-success);
}
</style>
