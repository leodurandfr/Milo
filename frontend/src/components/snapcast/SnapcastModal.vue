<!-- frontend/src/components/snapcast/SnapcastModal.vue -->
<template>
  <div class="snapcast-modal">
    <ModalHeader :title="$t('multiroom.title')">
      <template #actions>
        <Toggle 
          v-model="isMultiroomActive" 
          variant="primary" 
          :disabled="unifiedStore.isTransitioning"
          @change="handleMultiroomToggle" 
        />
      </template>
    </ModalHeader>

    <div class="main-content">
      <SnapcastControl />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import ModalHeader from '@/components/ui/ModalHeader.vue';
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

.main-content {
  flex: 1;
  overflow: visible;
}
</style>