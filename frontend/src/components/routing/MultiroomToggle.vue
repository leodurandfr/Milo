<!-- frontend/src/components/routing/MultiroomToggle.vue - Version refactorisée -->
<template>
  <Toggle
    v-model="isMultiroom"
    title="Audio Output"
    on-label="Multiroom"
    off-label="Direct"
    :status-text="statusText"
    :disabled="unifiedStore.isTransitioning"
    @change="handleToggle"
  />
</template>

<script setup>
import { computed } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import Toggle from '@/components/ui/Toggle.vue';

const unifiedStore = useUnifiedAudioStore();

const isMultiroom = computed({
  get: () => unifiedStore.multiroomEnabled,  // Refactorisé
  set: () => {} // Géré par handleToggle
});

const statusText = computed(() => 
  isMultiroom.value ? 'Audio via Snapserver' : 'Audio direct HiFiBerry'
);

async function handleToggle(enabled) {
  await unifiedStore.setMultiroomEnabled(enabled);  // Refactorisé
}
</script>