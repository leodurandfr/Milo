<!-- frontend/src/components/equalizer/ItemSelector.vue -->
<!-- Zone/Client selector (tabs only - volume controls moved to MultiroomControl) -->
<template>
  <div v-show="zoneTabs.length > 1" class="item-selector">
    <section class="settings-section tabs-section">
      <div class="tabs-container">
        <button
          v-for="tab in zoneTabs"
          :key="tab.value"
          type="button"
          class="tab-button heading-4"
          :class="{
            'tab-button--active': selectedTargetLocal === tab.value,
            'tab-button--disabled': tab.disabled
          }"
          :disabled="tab.disabled"
          @click="handleTargetChange(tab.value)"
        >
          <SvgIcon v-if="tab.badge" :name="tab.badge" :size="12" class="tab-badge" />
          {{ tab.label }}
        </button>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { useEqualizerStore } from '@/stores/equalizerStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import SvgIcon from '@/components/ui/SvgIcon.vue';

const equalizerStore = useEqualizerStore();
const audioStore = useUnifiedAudioStore();

const props = defineProps({
  disabled: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['targetChange']);

// Local state
const selectedTargetLocal = ref(equalizerStore.selectedTarget);

// === COMPUTED ===
const targets = computed(() => equalizerStore.availableTargets);

// Convert targets to tabs format (zones + individual clients)
const zoneTabs = computed(() => {
  const tabs = [];
  const multiroomEnabled = audioStore.systemState.multiroom_enabled;

  // When multiroom is disabled, only show local Milo
  if (!multiroomEnabled) {
    const localTarget = targets.value.find(t => t.is_local);
    if (localTarget) {
      return [{
        label: localTarget.name,
        value: localTarget.id,  // Use MAC address, not 'local'
        disabled: !localTarget.online
      }];
    }
    return [];
  }

  // Group linked clients into zones (multiroom enabled)
  const processedIds = new Set();

  for (const target of targets.value) {
    if (processedIds.has(target.id)) continue;

    const linkedIds = equalizerStore.getLinkedClientIds(target.id);

    if (linkedIds.length > 1) {
      // This is a zone - get names of linked clients (backend sorts local first)
      const linkedClients = linkedIds
        .map(id => targets.value.find(t => t.id === id))
        .filter(Boolean);

      // Find the group for this zone to get custom name
      const group = equalizerStore.getZoneGroup(target.id);

      // Use custom zone name if set, otherwise combine client names
      const zoneName = group?.name || (linkedClients.length > 0
        ? linkedClients.map(c => c.name).join(' + ')
        : target.name);

      tabs.push({
        label: zoneName,
        value: `zone:${linkedIds.join(',')}`,
        disabled: linkedClients.length === 0 || linkedClients.every(c => !c.online),
        groupId: group?.id || null
      });

      // Mark all linked clients as processed
      linkedIds.forEach(id => processedIds.add(id));
    } else {
      // Individual client
      tabs.push({
        label: target.name,
        value: target.id,
        disabled: !target.online
      });
      processedIds.add(target.id);
    }
  }

  return tabs;
});

// Check if current selection is a zone (multiple linked clients)
const isZoneSelected = computed(() => {
  return selectedTargetLocal.value?.startsWith('zone:') ?? false;
});

// Selected zone/client name for display in other sections
const selectedZoneName = computed(() => {
  const tab = zoneTabs.value.find(t => t.value === selectedTargetLocal.value);
  return tab ? tab.label : '';
});

// Selected client IDs (for level meters aggregation)
const selectedClientIds = computed(() => {
  if (!selectedTargetLocal.value) {
    return [];  // No target selected yet
  }
  if (isZoneSelected.value) {
    return selectedTargetLocal.value.replace('zone:', '').split(',');
  }
  return [selectedTargetLocal.value];
});

// === HANDLERS ===
async function handleTargetChange(targetValue) {
  selectedTargetLocal.value = targetValue;

  // If it's a zone, select the first client as the active equalizer target
  if (targetValue.startsWith('zone:')) {
    const clientIds = targetValue.replace('zone:', '').split(',');
    if (clientIds.length > 0) {
      await equalizerStore.selectTarget(clientIds[0]);
    }
  } else {
    await equalizerStore.selectTarget(targetValue);
  }

  emit('targetChange', targetValue);
}

// Sync local target with store
watch(() => equalizerStore.selectedTarget, (newTarget) => {
  // Don't override if we have a zone selected
  if (!selectedTargetLocal.value?.startsWith('zone:')) {
    selectedTargetLocal.value = newTarget;
  }
});

// Auto-select first tab if current selection doesn't exist
// immediate: true ensures this runs on initial render (not just on change)
watch(zoneTabs, (tabs) => {
  if (tabs.length > 0) {
    const currentTabExists = tabs.some(t => t.value === selectedTargetLocal.value);
    if (!currentTabExists) {
      selectedTargetLocal.value = tabs[0].value;
      handleTargetChange(tabs[0].value);
    }
  }
}, { immediate: true });

// Expose selectedZoneName and selectedClientIds for parent components
defineExpose({ selectedZoneName, selectedClientIds });
</script>

<style scoped>
.item-selector {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  padding: var(--space-05-fixed) var(--space-05);
}
.settings-section.tabs-section{
  border-radius: var(--radius-05);
}

/* === TABS SECTION === */
.tabs-section {
  padding: var(--space-02);
}

.tabs-container {
  display: flex;
  gap: var(--space-02);
  overflow-x: auto;
}

.tabs-container::-webkit-scrollbar {
  height: 4px;
}

.tabs-container::-webkit-scrollbar-thumb {
  background: var(--color-border);
  border-radius: 2px;
}

.tab-button {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-02);
  height: 40px;
  padding: 10px 16px;
  border: none;
  border-radius: var(--radius-03);
  cursor: pointer;
  white-space: nowrap;
  transition: background-color var(--transition-fast), color var(--transition-fast);
  /* Inactive state - outline */
  background-color: var(--color-background-neutral);
  color: var(--color-brand);
  box-shadow: inset 0 0 0 2px var(--color-brand);
}

.tab-button--active {
  background-color: var(--color-brand);
  color: var(--color-text-contrast);
  box-shadow: none;
}

.tab-button--disabled {
  background-color: var(--color-background);
  color: var(--color-text-light);
  box-shadow: none;
  cursor: not-allowed;
}

.tab-badge {
  opacity: 0.8;
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .settings-section {
    border-radius: var(--radius-05);
  }

  .tabs-container {
    gap: var(--space-01);
  }

  .tab-button {
    height: 38px;
    padding: 8px 14px;
  }
}
</style>
