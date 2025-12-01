<!-- frontend/src/components/multiroom/MultiroomModal.vue -->
<template>
  <div class="multiroom-modal">
    <ModalHeader :title="$t('multiroom.title')">
      <template #actions="{ iconType }">
        <Toggle
          v-model="isMultiroomActive"
          :type="iconType"
          :disabled="unifiedStore.systemState.transitioning || isToggling"
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
import { ref, computed, watch, onMounted, onUnmounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';
import ModalHeader from '@/components/ui/ModalHeader.vue';
import Toggle from '@/components/ui/Toggle.vue';
import MultiroomControl from './MultiroomControl.vue';

const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

const isToggling = ref(false);

const isMultiroomActive = computed(() => unifiedStore.systemState.multiroom_enabled);

async function handleMultiroomToggle(enabled) {
  await unifiedStore.setMultiroomEnabled(enabled);
}

// === WEBSOCKET HANDLERS ===
function handleMultiroomEnabling() {
  isToggling.value = true;
}

function handleMultiroomDisabling() {
  isToggling.value = true;
}

function handleMultiroomReady() {
  isToggling.value = false;
}

function handleMultiroomError() {
  isToggling.value = false;
}

let unsubscribeFunctions = [];

// Watch for state changes to reset toggling (handles disabling case)
watch(isMultiroomActive, (newValue, oldValue) => {
  if (!newValue && oldValue) {
    // Multiroom was disabled - reset toggle state
    isToggling.value = false;
  }
});

// === LIFECYCLE ===
onMounted(() => {
  unsubscribeFunctions.push(
    on('routing', 'multiroom_enabling', handleMultiroomEnabling),
    on('routing', 'multiroom_disabling', handleMultiroomDisabling),
    on('routing', 'multiroom_ready', handleMultiroomReady),
    on('routing', 'multiroom_error', handleMultiroomError)
  );
});

onUnmounted(() => {
  unsubscribeFunctions.forEach(unsubscribe => unsubscribe());
});
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
