<!-- frontend/src/components/settings/categories/DockVolumeSettings.vue -->
<!-- Dock › Volume: on which screens the dock offers the volume control -->
<template>
  <SectionStack>
    <SectionCard>
      <div class="surface-list">
        <ListItemButton
          v-for="surface in SURFACES"
          :key="surface.key"
          :title="t(surface.labelKey)"
          action="toggle"
          :model-value="settingsStore.dockVolume[surface.key]"
          @update:model-value="(value) => handleToggle(surface.key, value)"
        />
      </div>
    </SectionCard>

    <SettingsRefusal :detail="refusal" />
  </SectionStack>
</template>

<script setup>
import { useI18n } from '@/services/i18n';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import SectionStack from '@/components/ui/SectionStack.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import SettingsRefusal from '@/components/settings/SettingsRefusal.vue';

const SURFACES = [
  { key: 'milo_screen', labelKey: 'dockVolume.miloScreen' },
  { key: 'desktop_browser', labelKey: 'dockVolume.desktopBrowser' },
  { key: 'phone_browser', labelKey: 'dockVolume.phoneBrowser' },
  { key: 'ios_app', labelKey: 'dockVolume.iosApp' }
];

const { t } = useI18n();
// The switches draw the store, which a refused write reads back.
const { debouncedUpdate, refusal } = useSettingsAPI();
const settingsStore = useSettingsStore();

function handleToggle(surface, value) {
  settingsStore.updateDockVolume({ [surface]: value });
  debouncedUpdate('dock-volume', 'dock-volume', { ...settingsStore.dockVolume }, 300);
}
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
