<!-- frontend/src/components/snapcast/SnapcastModal.vue -->
<template>
  <div class="snapcast-modal">
    <div class="modal-header">
      <h2 class="heading-2">{{ $t('Multiroom') }}</h2>
      <Toggle 
        v-model="isMultiroomActive" 
        variant="primary" 
        :disabled="unifiedStore.isTransitioning"
        @change="handleMultiroomToggle" 
      />
    </div>

    <div class="main-content">
      <SnapcastControl />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import Toggle from '@/components/ui/Toggle.vue';
import SnapcastControl from './SnapcastControl.vue';

const unifiedStore = useUnifiedAudioStore();

const isMultiroomActive = computed(() => unifiedStore.multiroomEnabled);

async function handleMultiroomToggle(enabled) {
  await unifiedStore.setMultiroomEnabled(enabled);
}
</script>

<style scoped>
.snapcast-modal {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.modal-header {
  background: var(--color-background-contrast);
  border-radius: var(--radius-04);
  padding: var(--space-04) var(--space-05);
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.modal-header h2 {
  color: var(--color-text-contrast);
}

.main-content {
  flex: 1;
  overflow: visible;
}
</style>