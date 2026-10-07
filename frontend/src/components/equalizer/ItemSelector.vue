<!-- frontend/src/components/equalizer/ItemSelector.vue -->
<!-- Zone/Client selector (tabs only - volume controls moved to MultiroomControl) -->
<template>
  <ButtonGroup v-show="zoneTabs.length > 1" :model-value="activeTab?.value ?? null" :options="zoneTabs"
    mobile-layout="scroll" @change="handleTargetChange" />
</template>

<script setup>
import { computed, watch } from 'vue';
import { useEqualizerStore } from '@/stores/equalizerStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';

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

    const zone = multiroomStore.getZoneForClient(target.id);

    if (!zone) {
      // Standalone client
      tabs.push({
        label: target.name,
        value: target.id,
        memberIds: [target.id],
        disabled: !target.online
      });
      processedIds.add(target.id);
      continue;
    }

    // A zone member that detached its EQ is NOT folded into the zone tab — it
    // gets its own individual tab. The zone tab folds only the shared members,
    // and disappears entirely when every member has gone independent.
    const memberClients = zone.client_ids
      .map(id => targets.value.find(t => t.id === id))
      .filter(Boolean);
    const sharedClients = memberClients.filter(c => !multiroomStore.isClientEqIndependent(c.id));
    const independentClients = memberClients.filter(c => multiroomStore.isClientEqIndependent(c.id));

    if (sharedClients.length > 0) {
      const sharedIds = sharedClients.map(c => c.id);
      // Use custom zone name if set, otherwise combine the shared client names.
      const zoneName = zone.name || sharedClients.map(c => c.name).join(' + ');
      tabs.push({
        label: zoneName,
        // Backend sorts local first, so the representative client is stable.
        value: sharedIds[0],
        memberIds: sharedIds,
        disabled: sharedClients.every(c => !c.online)
      });
    }

    for (const c of independentClients) {
      tabs.push({
        label: c.name,
        value: c.id,
        memberIds: [c.id],
        disabled: !c.online
      });
    }

    // Mark all zone members processed (shared, independent, and any offline).
    zone.client_ids.forEach(id => processedIds.add(id));
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
async function handleTargetChange(value) {
  await equalizerStore.selectTarget(value);
}

// Nothing is lit when the store's target is not on the strip: a remote client
// while multiroom is off, or the first render before loadTargets() has run.
// immediate: true so that first render is covered too.
watch(zoneTabs, (tabs) => {
  if (tabs.length > 0 && !activeTab.value) {
    handleTargetChange(tabs[0].value);
  }
}, { immediate: true });

// Expose selectedZoneName and selectedClientIds for parent components
defineExpose({ selectedZoneName, selectedClientIds });
</script>

