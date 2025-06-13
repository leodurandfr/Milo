<!-- frontend/src/components/routing/EqualizerToggle.vue -->
<template>
  <Toggle
    v-model="isEqualizerEnabled"
    title="Audio Processing"
    on-label="Equalizer ON"
    off-label="Equalizer OFF"
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

const isEqualizerEnabled = computed({
  get: () => unifiedStore.equalizerEnabled,
  set: () => {} // Géré par handleToggle
});

const statusText = computed(() => 
  isEqualizerEnabled.value ? 'Audio avec égalisation' : 'Audio sans traitement'
);

async function handleToggle(enabled) {
  await unifiedStore.setEqualizerEnabled(enabled);
}
</script>