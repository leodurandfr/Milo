<!-- frontend/src/components/multiroom/MultiroomModal.vue -->
<template>
  <div class="multiroom-modal">
    <NavigationHeader :title="t('audioSources.multiroom')">
      <template #actions>
        <Toggle
          :modelValue="isMultiroomActive"
          :disabled="unifiedStore.systemState.transitioning || multiroomStore.isTransitioning"
          @change="handleMultiroomToggle"
        />
      </template>
    </NavigationHeader>

    <div class="main-content">
      <MultiroomControl />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import NavigationHeader from '@/components/ui/NavigationHeader.vue';
import Toggle from '@/components/ui/Toggle.vue';
import MultiroomControl from './MultiroomControl.vue';

const { t } = useI18n();
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
