<!-- frontend/src/components/multiroom/MultiroomModal.vue -->
<template>
  <div class="multiroom-modal">
    <ModalHeader :title="$t('audioSources.multiroom')">
      <template #actions="{ iconType }">
        <Toggle
          :modelValue="isMultiroomActive"
          :type="iconType"
          :disabled="unifiedStore.systemState.transitioning || multiroomStore.isTransitioning"
          @change="handleMultiroomToggle"
        />
      </template>
    </ModalHeader>

    <div class="main-content">
      <MultiroomControl />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import ModalHeader from '@/components/ui/ModalHeader.vue';
import Toggle from '@/components/ui/Toggle.vue';
import MultiroomControl from './MultiroomControl.vue';

const unifiedStore = useUnifiedAudioStore();
const multiroomStore = useMultiroomStore();

const isMultiroomActive = computed(() => unifiedStore.systemState.multiroom_enabled);

async function handleMultiroomToggle(enabled) {
  await unifiedStore.setMultiroomEnabled(enabled);
}
</script>

<style scoped>
.multiroom-modal {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.main-content {
  display: flex;
  flex-direction: column;
}
</style>
