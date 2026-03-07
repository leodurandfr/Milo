<!-- frontend/src/components/settings/categories/DockSettings.vue -->
<template>
  <SettingsContainer>
    <!-- Audio sources -->
    <SettingsSection class="audio-sources-section">
      <template #header>
        <div class="section-header">
          <h2 class="heading-2">{{ t('audioSources.title') }}</h2>
          <Button
            size="small"
            :variant="isReordering ? 'brand' : 'background-strong'"
            @click="toggleReorderMode"
          >
            {{ isReordering ? t('applicationsSettings.done') : t('applicationsSettings.reorder') }}
          </Button>
        </div>
      </template>

      <div class="app-list" :class="{ 'app-list--reordering': isReordering }">
        <div
          v-for="(source, index) in localOrder"
          :key="source"
          class="drag-item"
          :class="{
            'drag-item--reordering': isReordering,
            'drag-item--dragging': dragState.index === index,
            'drag-item--transition': dragState.index !== -1 && dragState.index !== index,
            'drag-item--inactive': isReordering && !config[source]
          }"
          :style="getDragItemStyle(index)"
          @pointerdown="isReordering ? onDragStart($event, index) : null"
        >
          <ListItemButton
            :title="getSourceTitle(source)"
            :model-value="config[source]"
            variant="background"
            :action="isReordering ? 'none' : 'toggle'"
            :disabled="!isReordering && !canDisableAudioSource(source)"
            @update:model-value="(val) => handleToggle(source, val)"
          >
            <template #icon>
              <AppIcon :name="source" :size="40" />
            </template>
          </ListItemButton>

          <!-- Drag handle icon (reorder mode only) -->
          <div v-if="isReordering" class="drag-handle">
            <SvgIcon name="dragHandle" :size="24" />
          </div>
        </div>
      </div>
    </SettingsSection>

    <!-- Features -->
    <SettingsSection :title="t('applications.features')">
      <div class="app-list">
        <ListItemButton
          :title="t('equalizer.title')"
          :model-value="config.equalizer"
          variant="background"
          action="toggle"
          @update:model-value="(val) => handleToggle('equalizer', val)"
        >
          <template #icon>
            <AppIcon name="equalizer" :size="40" />
          </template>
        </ListItemButton>

        <ListItemButton
          :title="t('audioSources.multiroom')"
          :model-value="config.multiroom"
          variant="background"
          action="toggle"
          @update:model-value="(val) => handleToggle('multiroom', val)"
        >
          <template #icon>
            <AppIcon name="multiroom" :size="40" />
          </template>
        </ListItemButton>

        <ListItemButton
          :title="t('common.settings')"
          :model-value="config.settings"
          variant="background"
          action="toggle"
          @update:model-value="(val) => handleToggle('settings', val)"
        >
          <template #icon>
            <AppIcon name="settings" :size="40" />
          </template>
        </ListItemButton>
      </div>
    </SettingsSection>

    <!-- Inactivity timeout -->
    <ToggleSection
      :title="t('applicationsSettings.inactivityTimeout')"
      :enabled="inactivityEnabled"
      @change="handleInactivityToggle"
    >
      <SettingItem :label="t('applicationsSettings.inactivityDelay')">
        <ButtonGroup
          :model-value="inactivityConfig.inactivity_timeout"
          :options="inactivityPresets"
          mobile-layout="grid-3"
          @change="setInactivityTimeout"
        />
      </SettingItem>
    </ToggleSection>
  </SettingsContainer>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue';
import { storeToRefs } from 'pinia';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import AppIcon from '@/components/ui/AppIcon.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import Button from '@/components/ui/Button.vue';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import ToggleSection from '@/components/ui/ToggleSection.vue';

const { t } = useI18n();
const { on } = useWebSocket();
const { debouncedUpdate, updateSetting } = useSettingsAPI();
const settingsStore = useSettingsStore();

const { dockApps: config, sourceOrder } = storeToRefs(settingsStore);

const AUDIO_SOURCES = ['spotify', 'bluetooth', 'radio', 'podcast', 'airplay', 'mac'];

// === Source title mapping ===

function getSourceTitle(source) {
  const titles = {
    spotify: t('audioSources.spotify'),
    bluetooth: t('audioSources.bluetooth'),
    radio: t('audioSources.radio'),
    podcast: t('audioSources.podcasts'),
    airplay: t('audioSources.airplay'),
    mac: t('audioSources.macOS'),
  };
  return titles[source] || source;
}

// === Dock Apps ===

function canDisableAudioSource(sourceId) {
  const enabledAudioSources = AUDIO_SOURCES.filter(source =>
    config.value[source] && source !== sourceId
  );
  return enabledAudioSources.length > 0;
}

function saveDockApps() {
  const enabledApps = settingsStore.buildEnabledAppsArray();
  debouncedUpdate('dock-apps', 'dock-apps', { enabled_apps: enabledApps }, 500);
}

function handleToggle(appName, value) {
  config.value[appName] = value;
  saveDockApps();
}

// === Reorder Mode ===

const isReordering = ref(false);
const reorderList = ref([]);
const initialOrder = ref([]);
const localOrder = computed(() => isReordering.value ? reorderList.value : sourceOrder.value);

function toggleReorderMode() {
  if (isReordering.value) {
    // Exiting reorder mode — save if order changed
    isReordering.value = false;
    const changed = reorderList.value.some((s, i) => s !== initialOrder.value[i]);
    if (changed) {
      settingsStore.updateSourceOrder(reorderList.value);
      saveDockApps();
    }
  } else {
    // Entering reorder mode
    reorderList.value = [...sourceOrder.value];
    initialOrder.value = [...sourceOrder.value];
    isReordering.value = true;
  }
}

// === Drag and Drop ===

const dragState = ref({ index: -1, startY: 0, currentY: 0, itemHeight: 0 });

function getDragItemStyle(index) {
  if (dragState.value.index === -1) return {};

  if (index === dragState.value.index) {
    const offsetY = dragState.value.currentY - dragState.value.startY;
    return {
      transform: `translateY(${offsetY}px) scale(1.02)`,
      zIndex: 10,
      position: 'relative',
      transition: 'none'
    };
  }
  return {};
}

function onDragStart(e, index) {
  if (!isReordering.value) return;
  e.preventDefault();

  const target = e.currentTarget;
  const itemHeight = target.offsetHeight;

  dragState.value = {
    index,
    startY: e.clientY,
    currentY: e.clientY,
    itemHeight
  };

  document.addEventListener('pointermove', onDragMove);
  document.addEventListener('pointerup', onDragEnd);
  document.addEventListener('pointercancel', onDragEnd);
}

function onDragMove(e) {
  if (dragState.value.index === -1) return;

  dragState.value.currentY = e.clientY;
  const deltaY = e.clientY - dragState.value.startY;
  const threshold = dragState.value.itemHeight * 0.5;
  const draggedIndex = dragState.value.index;

  if (deltaY > threshold && draggedIndex < reorderList.value.length - 1) {
    swap(draggedIndex, draggedIndex + 1);
    dragState.value.index = draggedIndex + 1;
    dragState.value.startY += dragState.value.itemHeight;
  } else if (deltaY < -threshold && draggedIndex > 0) {
    swap(draggedIndex, draggedIndex - 1);
    dragState.value.index = draggedIndex - 1;
    dragState.value.startY -= dragState.value.itemHeight;
  }
}

function onDragEnd() {
  dragState.value = { index: -1, startY: 0, currentY: 0, itemHeight: 0 };
  document.removeEventListener('pointermove', onDragMove);
  document.removeEventListener('pointerup', onDragEnd);
  document.removeEventListener('pointercancel', onDragEnd);
}

function swap(i, j) {
  const arr = reorderList.value;
  [arr[i], arr[j]] = [arr[j], arr[i]];
}

// === Inactivity Timeout ===

const inactivityConfig = ref({
  inactivity_timeout: 7200
});

const inactivityEnabled = computed(() => inactivityConfig.value.inactivity_timeout !== 0);

const lastNonZeroTimeout = ref(7200);

function syncInactivityFromStore() {
  const timeout = settingsStore.inactivityTimeout.inactivity_timeout;
  inactivityConfig.value.inactivity_timeout = timeout;
  if (timeout > 0) {
    lastNonZeroTimeout.value = timeout;
  }
}

const inactivityPresets = computed(() => [
  { value: 300, label: t('time.5min') },
  { value: 900, label: t('time.15min') },
  { value: 3600, label: t('time.1h') },
  { value: 7200, label: t('time.2h') },
  { value: 14400, label: t('time.4h') },
  { value: 43200, label: t('time.12h') }
]);

function handleInactivityToggle(enabled) {
  if (enabled) {
    inactivityConfig.value.inactivity_timeout = lastNonZeroTimeout.value;
    settingsStore.updateInactivityTimeout({ inactivity_timeout: lastNonZeroTimeout.value });
    updateSetting('inactivity-timeout', { inactivity_timeout: lastNonZeroTimeout.value });
  } else {
    if (inactivityConfig.value.inactivity_timeout > 0) {
      lastNonZeroTimeout.value = inactivityConfig.value.inactivity_timeout;
    }
    inactivityConfig.value.inactivity_timeout = 0;
    settingsStore.updateInactivityTimeout({ inactivity_timeout: 0 });
    updateSetting('inactivity-timeout', { inactivity_timeout: 0 });
  }
}

function setInactivityTimeout(value) {
  if (value > 0) {
    lastNonZeroTimeout.value = value;
  }
  inactivityConfig.value.inactivity_timeout = value;
  settingsStore.updateInactivityTimeout({ inactivity_timeout: value });
  updateSetting('inactivity-timeout', { inactivity_timeout: value });
}

// Sync reorder list when sourceOrder changes externally (e.g. WS dock_apps_changed)
watch(sourceOrder, (newOrder) => {
  if (isReordering.value) {
    reorderList.value = [...newOrder];
  }
});

// === WebSocket listeners ===

const handleInactivityTimeoutChanged = (msg) => {
  if (msg.data?.config?.inactivity_timeout !== undefined) {
    const timeout = msg.data.config.inactivity_timeout;
    settingsStore.updateInactivityTimeout({ inactivity_timeout: timeout });
    inactivityConfig.value.inactivity_timeout = timeout;
    if (timeout > 0) {
      lastNonZeroTimeout.value = timeout;
    }
  }
};

onMounted(() => {
  syncInactivityFromStore();
  on('settings', 'inactivity_timeout_changed', handleInactivityTimeoutChanged);
});

onUnmounted(() => {
  document.removeEventListener('pointermove', onDragMove);
  document.removeEventListener('pointerup', onDragEnd);
  document.removeEventListener('pointercancel', onDragEnd);
});
</script>

<style scoped>
.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.app-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-01);
}

.app-list--reordering {
  display: flex;
  flex-direction: column;
}

.drag-item {
  position: relative;
  display: flex;
  align-items: stretch;
  transition: transform var(--transition-spring);
}

.drag-item--reordering {
  cursor: grab;
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
}

.drag-item--dragging {
  z-index: 10;
  opacity: 0.92;
  cursor: grabbing;
}

.drag-item--transition {
  transition: transform var(--transition-spring);
}

.drag-item :deep(.list-item-button) {
  flex: 1;
}

.drag-item--reordering :deep(.list-item-button) {
  pointer-events: none;
}

.drag-item--inactive :deep(.list-item-button__title) {
  color: var(--color-text-secondary);
}

.drag-handle {
  position: absolute;
  right: var(--space-03);
  top: 0;
  bottom: 0;
  display: flex;
  align-items: center;
  color: var(--color-text-light);
  pointer-events: none;
}

:deep(.audio-sources-section.settings-section) {
  gap: var(--space-03);
}

@media (max-aspect-ratio: 4/3) {
  .app-list {
    display: flex;
    flex-direction: column;
  }
}
</style>
