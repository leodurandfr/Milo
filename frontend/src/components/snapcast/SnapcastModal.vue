<!-- frontend/src/components/snapcast/SnapcastModal.vue -->
<template>
  <div class="snapcast-modal">
    <ModalHeader :title="$t('multiroom.title')">
      <template #actions="{ iconType }">
        <Toggle
          v-model="isMultiroomActive"
          :type="iconType"
          :disabled="unifiedStore.systemState.transitioning || isMultiroomToggling"
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
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';
import ModalHeader from '@/components/ui/ModalHeader.vue';
import Toggle from '@/components/ui/Toggle.vue';
import SnapcastControl from './SnapcastControl.vue';

const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

const isMultiroomToggling = ref(false);

const isMultiroomActive = computed(() => unifiedStore.systemState.multiroom_enabled);

async function handleMultiroomToggle(enabled) {
  await unifiedStore.setMultiroomEnabled(enabled);
}

// === WEBSOCKET HANDLERS ===
function handleMultiroomEnabling() {
  isMultiroomToggling.value = true;
}

function handleMultiroomDisabling() {
  isMultiroomToggling.value = true;
}

// Watcher multiroom state
let lastMultiroomState = isMultiroomActive.value;
const watcherInterval = setInterval(() => {
  if (lastMultiroomState !== isMultiroomActive.value) {
    lastMultiroomState = isMultiroomActive.value;

    isMultiroomToggling.value = false;
  }
}, 100);

let unsubscribeFunctions = [];

// === LIFECYCLE ===
onMounted(() => {
  unsubscribeFunctions.push(
    on('routing', 'multiroom_enabling', handleMultiroomEnabling),
    on('routing', 'multiroom_disabling', handleMultiroomDisabling)
  );
});

onUnmounted(() => {
  unsubscribeFunctions.forEach(unsubscribe => unsubscribe());
  clearInterval(watcherInterval);
});
</script>

<style scoped>
.snapcast-modal {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.main-content {
  display: flex;
  flex-direction: column;
}
</style>