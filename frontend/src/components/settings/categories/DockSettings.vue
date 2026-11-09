<!-- frontend/src/components/settings/categories/DockSettings.vue -->
<template>
  <SectionStack>
    <SectionCard
      v-for="section in sections"
      :key="section.id"
      class="dock-section"
    >
      <template #header>
        <SectionHeader :title="section.title">
          <template #actions>
            <Button
              size="small"
              :variant="section.isReordering ? 'brand' : 'tinted'"
              @click="section.toggleReorder()"
            >
              {{ section.isReordering ? t('applicationsSettings.done') : t('applicationsSettings.reorder') }}
            </Button>
          </template>
        </SectionHeader>
      </template>

      <div
        :ref="el => { section.container = el; }"
        class="app-list"
        :class="{ 'app-list--dragging': section.drag.dragging }"
      >
        <div
          v-for="(id, index) in section.items"
          :key="id"
          class="drag-item"
          :class="{
            'drag-item--reordering': section.isReordering,
            'drag-item--dragging': section.drag.draggedIndex === index,
            'drag-item--inactive': section.isReordering && !config[id]
          }"
          :style="section.drag.itemStyle(index)"
          @pointerdown="section.isReordering ? section.drag.onPointerDown($event, index) : null"
        >
          <ListItemButton
            :title="section.titleOf(id)"
            :model-value="config[id]"
            :action="section.isReordering ? 'none' : 'toggle'"
            :disabled="!section.isReordering && !section.canDisable(id)"
            @update:model-value="(val) => handleToggle(id, val)"
          >
            <template #icon>
              <AppIcon :name="id" :size="40" />
            </template>
          </ListItemButton>

          <!-- Drag handle icon (reorder mode only) -->
          <div v-if="section.isReordering" class="drag-handle">
            <SvgIcon name="dragHandle" :size="24" />
          </div>
        </div>
      </div>
    </SectionCard>
  </SectionStack>
</template>

<script setup>
import { ref, computed, reactive, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { useI18n } from '@/services/i18n';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSlotReorder } from '@/composables/useSlotReorder';
import { useSettingsStore } from '@/stores/settingsStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import AppIcon from '@/components/ui/AppIcon.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import Button from '@/components/ui/Button.vue';
import SectionStack from '@/components/ui/SectionStack.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import SectionHeader from '@/components/ui/SectionHeader.vue';
import { ALL_AUDIO_SOURCES, AUDIO_SOURCE_LABEL_KEYS } from '@/constants/audioSources';
import { DOCK_FEATURE_LABEL_KEYS } from '@/constants/dockFeatures';

const { t } = useI18n();
const { debouncedUpdate } = useSettingsAPI();
const settingsStore = useSettingsStore();
const unifiedStore = useUnifiedAudioStore();

const { dockApps: config, sourceOrder, featureOrder } = storeToRefs(settingsStore);

// === Dock Apps ===

function canDisableAudioSource(sourceId) {
  return ALL_AUDIO_SOURCES.some(source => config.value[source] && source !== sourceId);
}

function saveDockApps() {
  const enabledApps = settingsStore.buildEnabledAppsArray();
  // The tile moved before the request: a rejection the server announces no
  // dock for (network, a failed save) is undone by reading the stored one.
  debouncedUpdate('dock-apps', 'dock-apps', { enabled_apps: enabledApps }, 500, {
    onError: () => settingsStore.loadAllSettings()
  });
}

function handleToggle(appName, value) {
  config.value[appName] = value;
  saveDockApps();
}

// === Reorderable sections ===

/**
 * One reorderable list of the dock: its roster in the store's order, the ids
 * shown (an app hidden here keeps its place in the roster), and a reorder mode
 * that edits a local copy and saves it once, on Done.
 */
function useDockSection({ id, titleKey, labelKeys, order, isShown, canDisable, commit }) {
  const container = ref(null);
  const isReordering = ref(false);
  const draft = ref([]);
  const shown = computed(() => order.value.filter(isShown));
  const items = computed(() => (isReordering.value ? draft.value : shown.value));

  const drag = useSlotReorder(container, {
    onDrop: (from, to) => {
      const next = [...draft.value];
      next.splice(to, 0, ...next.splice(from, 1));
      draft.value = next;
    }
  });

  function toggleReorder() {
    if (!isReordering.value) {
      draft.value = [...shown.value];
      isReordering.value = true;
      return;
    }
    isReordering.value = false;
    if (draft.value.some((app, i) => app !== shown.value[i])) {
      // The hidden ids keep their slots; the shown ones fill the rest in the new order.
      let i = 0;
      commit(order.value.map(app => (isShown(app) ? draft.value[i++] : app)));
      saveDockApps();
    }
  }

  // What is shown changed under the draft (an order saved elsewhere, a feature
  // hidden or back): it restarts, or Done would merge a draft of another length.
  // Keyed on the content, since volumeState is replaced on every level change.
  watch(() => shown.value.join(), () => {
    if (isReordering.value) draft.value = [...shown.value];
  });

  return reactive({
    id,
    title: computed(() => t(titleKey)),
    titleOf: app => t(labelKeys[app]),
    canDisable,
    container,
    isReordering,
    items,
    drag,
    toggleReorder
  });
}

const sections = [
  useDockSection({
    id: 'sources',
    titleKey: 'audioSources.title',
    labelKeys: AUDIO_SOURCE_LABEL_KEYS,
    order: sourceOrder,
    isShown: () => true,
    canDisable: canDisableAudioSource,
    commit: settingsStore.updateSourceOrder
  }),
  useDockSection({
    id: 'features',
    titleKey: 'applications.features',
    labelKeys: DOCK_FEATURE_LABEL_KEYS,
    order: featureOrder,
    // Hidden with nothing to set a level on, as the tile is in the dock
    isShown: app => app !== 'volume' || unifiedStore.volumeState.any_volume_control,
    canDisable: () => true,
    commit: settingsStore.updateFeatureOrder
  })
];
</script>

<style scoped>
/* The same layout in reorder mode: the items move between the slots it gives them */
.app-list {
  position: relative;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-01);
}

.drag-item {
  position: relative;
  display: flex;
  align-items: stretch;
}

.drag-item--reordering {
  cursor: grab;
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
  transition: transform var(--transition-spring-soft);
}

.app-list--dragging .drag-item {
  cursor: grabbing;
}

.drag-item--dragging {
  opacity: 0.92;
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
  color: var(--color-text-tertiary);
  pointer-events: none;
}

:deep(.dock-section.section-card) {
  gap: var(--space-03);
}

@media (max-aspect-ratio: 4/3) and (max-width: 1023px) {
  .app-list {
    display: flex;
    flex-direction: column;
  }
}
</style>
