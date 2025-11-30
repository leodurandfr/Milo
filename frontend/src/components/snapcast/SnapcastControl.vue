<!-- frontend/src/components/snapcast/SnapcastControl.vue -->
<template>
  <div class="clients-container">
    <div class="clients-list">
      <!-- Single Transition for both states -->
      <Transition name="fade-slide" mode="out-in">
        <!-- MESSAGE: Multiroom disabled -->
        <MessageContent v-if="showMessage" key="message" icon="multiroom" :title="$t('multiroom.disabled')" />

        <!-- CLIENTS: Skeletons OR real items -->
        <div v-else key="clients" class="clients-wrapper">
          <SnapclientItem v-for="(client, index) in displayClients" :key="index" :client="client"
            :is-loading="shouldShowLoading" @volume-change="handleVolumeChange" @mute-toggle="handleMuteToggle" />
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useSnapcastStore } from '@/stores/snapcastStore';
import useWebSocket from '@/services/websocket';
import SnapclientItem from './SnapclientItem.vue';
import MessageContent from '@/components/ui/MessageContent.vue';

const unifiedStore = useUnifiedAudioStore();
const snapcastStore = useSnapcastStore();
const { on } = useWebSocket();

// Local state for multiroom transitions
const isMultiroomTransitioning = ref(false);
const isMultiroomDeactivating = ref(false);

let unsubscribeFunctions = [];

// === COMPUTED ===
const isMultiroomActive = computed(() => unifiedStore.systemState.multiroom_enabled);

// Local state for toggling
const isTogglingMultiroom = ref(false);

const showMessage = computed(() => {
  // If we are deactivating, show the message immediately
  if (isTogglingMultiroom.value && isMultiroomDeactivating.value) {
    return true;
  }

  // During activation, do not show the message
  if (isTogglingMultiroom.value || isMultiroomTransitioning.value) {
    return false;
  }

  return !isMultiroomActive.value;
});

// Force loading mode during toggling or loading
// But not during deactivation (we just let the fade-out happen)
const shouldShowLoading = computed(() => {
  if (isTogglingMultiroom.value && isMultiroomDeactivating.value) {
    return false; // No skeletons during deactivation
  }
  return snapcastStore.isLoading || isTogglingMultiroom.value;
});

const displayClients = computed(() => {
  // If we are activating multiroom, show empty placeholders for skeletons
  if (isTogglingMultiroom.value && !isMultiroomDeactivating.value) {
    return Array.from({ length: snapcastStore.lastKnownClientCount }, (_, i) => ({
      id: `placeholder-${i}`,
      name: '',
      volume: 0,
      muted: false
    }));
  }

  if (snapcastStore.clients.length === 0 && snapcastStore.isLoading) {
    return Array.from({ length: snapcastStore.lastKnownClientCount }, (_, i) => ({
      id: `placeholder-${i}`,
      name: '',
      volume: 0,
      muted: false
    }));
  }
  return snapcastStore.clients;
});

// === HANDLERS ===
async function handleVolumeChange(clientId, volume) {
  await snapcastStore.updateClientVolume(clientId, volume);
}

async function handleMuteToggle(clientId, muted) {
  await snapcastStore.toggleClientMute(clientId, muted);
}

// === WEBSOCKET HANDLERS ===
function handleClientConnected(event) {
  if (!isMultiroomDeactivating.value) {
    snapcastStore.handleClientConnected(event);
  }
}

function handleClientDisconnected(event) {
  if (!isMultiroomDeactivating.value) {
    snapcastStore.handleClientDisconnected(event);
  }
}

function handleClientVolumeChanged(event) {
  snapcastStore.handleClientVolumeChanged(event);
}

function handleClientNameChanged(event) {
  snapcastStore.handleClientNameChanged(event);
}

function handleClientMuteChanged(event) {
  snapcastStore.handleClientMuteChanged(event);
}

function handleSystemStateChanged(event) {
  unifiedStore.updateState(event);
}

async function handleMultiroomEnabling() {
  isTogglingMultiroom.value = true;
  isMultiroomTransitioning.value = true;
  snapcastStore.isLoading = true;
  snapcastStore.clearCache();
}

async function handleMultiroomDisabling() {
  isTogglingMultiroom.value = true;
  isMultiroomDeactivating.value = true;
  snapcastStore.isLoading = false;
  // Clients will be cleared after the end of toggling via the watcher
}

// === LIFECYCLE ===
onMounted(async () => {
  if (isMultiroomTransitioning.value && isMultiroomActive.value) {
    isMultiroomTransitioning.value = false;
  }

  if (isMultiroomDeactivating.value && !isMultiroomActive.value) {
    isMultiroomDeactivating.value = false;
  }

  if (isMultiroomActive.value) {
    // Preload cache synchronously to get the correct number of clients
    snapcastStore.preloadCache();
    // Load fresh clients in the background
    await snapcastStore.loadClients();
  }

  unsubscribeFunctions.push(
    on('snapcast', 'client_connected', handleClientConnected),
    on('snapcast', 'client_disconnected', handleClientDisconnected),
    on('snapcast', 'client_volume_changed', handleClientVolumeChanged),
    on('snapcast', 'client_name_changed', handleClientNameChanged),
    on('snapcast', 'client_mute_changed', handleClientMuteChanged),
    on('system', 'state_changed', handleSystemStateChanged),
    on('routing', 'multiroom_enabling', handleMultiroomEnabling),
    on('routing', 'multiroom_disabling', handleMultiroomDisabling)
  );
});

onUnmounted(() => {
  unsubscribeFunctions.forEach(unsubscribe => unsubscribe());
});

// === WATCHERS ===
// Watcher to detect when the store is transitioning (API call in progress)
watch(() => unifiedStore.systemState.transitioning, (isTransitioning, wasTransitioning) => {
  if (isTransitioning && !wasTransitioning) {
    // Start of a transition: trigger the animation immediately
    const currentMultiroomState = isMultiroomActive.value;

    if (currentMultiroomState) {
      // We are deactivating multiroom
      isTogglingMultiroom.value = true;
      isMultiroomDeactivating.value = true;
      snapcastStore.isLoading = false;
    } else {
      // We are activating multiroom
      isTogglingMultiroom.value = true;
      isMultiroomTransitioning.value = true;
      snapcastStore.isLoading = true;
      snapcastStore.clearCache();
    }
  }
});

watch(isMultiroomActive, async (newValue, oldValue) => {
  if (newValue && !oldValue) {
    isMultiroomDeactivating.value = false;

    const forceNoCache = isMultiroomTransitioning.value;
    await snapcastStore.loadClients(forceNoCache);

    isMultiroomTransitioning.value = false;
    isTogglingMultiroom.value = false;
  } else if (!newValue && oldValue) {
    // Deactivation: reset immediately to show the message
    isMultiroomDeactivating.value = false;
    isTogglingMultiroom.value = false;
  }
});

// Clean clients after deactivation toggling ends
watch(isTogglingMultiroom, (isToggling, wasToggling) => {
  if (!isToggling && wasToggling && !isMultiroomActive.value) {
    // End of deactivation toggling: clear clients
    snapcastStore.clients = [];
    snapcastStore.clearCache();
  }
});
</script>

<style scoped>
.clients-list {
  display: flex;
  flex-direction: column;
  position: relative;
}

.clients-wrapper {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}
</style>