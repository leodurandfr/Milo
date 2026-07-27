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
            'tab-button--active': tab === activeTab,
            'tab-button--disabled': tab.disabled
          }"
          :disabled="tab.disabled"
          @click="handleTargetChange(tab)"
        >
          <SvgIcon v-if="tab.badge" :name="tab.badge" :size="12" class="tab-badge" />
          {{ tab.label }}
        </button>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, watch } from 'vue';
import { useEqualizerStore } from '@/stores/equalizerStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import SvgIcon from '@/components/ui/SvgIcon.vue';

const equalizerStore = useEqualizerStore();
const multiroomStore = useMultiroomStore();
const audioStore = useUnifiedAudioStore();

// === COMPUTED ===
const targets = computed(() => equalizerStore.availableTargets);

// Convert targets to tabs format (zones + individual clients).
// A tab is addressed by one of its clients — the store holds a client MAC and
// derives the zone from it (equalizerStore.targetRef()), so a tab carries the
// members it folds in rather than a second spelling of "which target is this".
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
        memberIds: [localTarget.id],
        disabled: !localTarget.online
      }];
    }
    return [];
  }

  // Group linked clients into zones (multiroom enabled)
  const processedIds = new Set();

  for (const target of targets.value) {
    if (processedIds.has(target.id)) continue;

    const linkedIds = multiroomStore.getLinkedClientIds(target.id);

    if (linkedIds.length > 1) {
      // This is a zone - get names of linked clients (backend sorts local first)
      const linkedClients = linkedIds
        .map(id => targets.value.find(t => t.id === id))
        .filter(Boolean);

      // Find the zone for this client to get its custom name
      const group = multiroomStore.getZoneForClient(target.id);

      // Use custom zone name if set, otherwise combine client names
      const zoneName = group?.name || (linkedClients.length > 0
        ? linkedClients.map(c => c.name).join(' + ')
        : target.name);

      tabs.push({
        label: zoneName,
        // Backend sorts local first, so the representative client is stable.
        value: linkedIds[0],
        memberIds: linkedIds,
        disabled: linkedClients.length === 0 || linkedClients.every(c => !c.online)
      });

      // Mark all linked clients as processed
      linkedIds.forEach(id => processedIds.add(id));
    } else {
      // Individual client
      tabs.push({
        label: target.name,
        value: target.id,
        memberIds: [target.id],
        disabled: !target.online
      });
      processedIds.add(target.id);
    }
  }

  return tabs;
});

// The store's selected client decides which tab is lit — no local mirror.
const activeTab = computed(
  () => zoneTabs.value.find(tab => tab.memberIds.includes(equalizerStore.selectedTarget)) ?? null
);

// Selected zone/client name for display in other sections
const selectedZoneName = computed(() => activeTab.value?.label ?? '');

// Selected client IDs (for level meters aggregation)
const selectedClientIds = computed(() => activeTab.value?.memberIds ?? []);

// === HANDLERS ===
async function handleTargetChange(tab) {
  await equalizerStore.selectTarget(tab.value);
}

// Nothing is lit when the store's target is not on the strip: a remote client
// while multiroom is off, or the first render before loadTargets() has run.
// immediate: true so that first render is covered too.
watch(zoneTabs, (tabs) => {
  if (tabs.length > 0 && !activeTab.value) {
    handleTargetChange(tabs[0]);
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
