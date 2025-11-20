<!-- frontend/src/components/snapcast/SnapcastControl.vue -->
<template>
  <div class="clients-container" :class="{ opening: isOpening }" :style="{ height: containerHeight }">
    <div class="clients-list" ref="clientsListRef" :class="{ 'with-background': showBackground }">
      <!-- MESSAGE: Multiroom disabled -->
      <Transition name="message">
        <div v-if="showMessage" key="message" class="message-content">
          <SvgIcon name="multiroom" :size="96" color="var(--color-background-medium-16)" />
          <p class="text-mono">{{ $t("multiroom.disabled") }}</p>
        </div>
      </Transition>

      <!-- CLIENTS: Skeletons OR real items -->
      <Transition name="clients">
        <div v-if="!showMessage" key="clients" class="clients-wrapper">
          <SnapclientItem v-for="(client, index) in displayClients" :key="index" :client="client"
            :is-loading="shouldShowLoading" @volume-change="handleVolumeChange" @mute-toggle="handleMuteToggle" />
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useSnapcastStore } from '@/stores/snapcastStore';
import useWebSocket from '@/services/websocket';
import SnapclientItem from './SnapclientItem.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';

const unifiedStore = useUnifiedAudioStore();
const snapcastStore = useSnapcastStore();
const { on } = useWebSocket();

const clientsListRef = ref(null);
const containerHeight = ref('0px');
const isOpening = ref(true);

// Local state for multiroom transitions
const isMultiroomTransitioning = ref(false);
const isMultiroomDeactivating = ref(false);

// Height constants
const ITEM_HEIGHT_DESKTOP = 72;
const ITEM_HEIGHT_MOBILE = 116;
const GAP_DESKTOP = 8;
const GAP_MOBILE = 8;

let resizeObserver = null;
let unsubscribeFunctions = [];

function calculateInitialHeight(clientsCount) {
  const isMobile = window.matchMedia('(max-aspect-ratio: 4/3)').matches;
  const itemHeight = isMobile ? ITEM_HEIGHT_MOBILE : ITEM_HEIGHT_DESKTOP;
  const gap = isMobile ? GAP_MOBILE : GAP_DESKTOP;

  return (clientsCount * itemHeight) + ((clientsCount - 1) * gap);
}

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

const showBackground = computed(() => {
  return showMessage.value;
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

// === RESIZE OBSERVER ===
function setupResizeObserver() {
  if (resizeObserver) {
    resizeObserver.disconnect();
  }

  resizeObserver = new ResizeObserver(entries => {
    if (entries[0]) {
      const newHeight = entries[0].contentRect.height;
      const currentHeight = parseFloat(containerHeight.value);

      if (Math.abs(newHeight - currentHeight) > 2) {
        containerHeight.value = `${newHeight}px`;
      }
    }
  });

  if (clientsListRef.value) {
    resizeObserver.observe(clientsListRef.value);
  }
}

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
    // Set the height immediately with the correct number of clients
    containerHeight.value = `${calculateInitialHeight(snapcastStore.clients.length || snapcastStore.lastKnownClientCount)}px`;
    // Load fresh clients in the background
    await snapcastStore.loadClients();
  } else {
    containerHeight.value = `${calculateInitialHeight(snapcastStore.lastKnownClientCount)}px`;
  }

  await nextTick();
  setupResizeObserver();

  // Disable the 'opening' class after the modal animation
  setTimeout(() => {
    isOpening.value = false;
  }, 700);

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
  if (resizeObserver) {
    resizeObserver.disconnect();
  }
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
.clients-container {
  transition: height var(--transition-spring);
  overflow: visible;
  position: relative;
}

.clients-container.opening {
  transition: none !important;
}

.clients-list {
  display: flex;
  flex-direction: column;
  overflow: visible;
  border-radius: var(--radius-04);
  transition: 400ms ease;
  position: relative;
}

.clients-list.with-background {
  background: var(--color-background-neutral);
}

.message-content {
  display: flex;
  min-height: 232px;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: var(--space-04);
  padding: var(--space-05);
}

.message-content .text-mono {
  text-align: center;
  color: var(--color-text-secondary);
}

.clients-wrapper {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.message-enter-active {
  transition: opacity 300ms ease, transform 300ms ease;
}

.message-leave-active {
  transition: opacity 300ms ease, transform 300ms ease;
  position: absolute;
  width: 100%;
}

.message-enter-from {
  opacity: 0;
  transform: translateY(12px);
}

.message-leave-to {
  opacity: 0;
  transform: translateY(-12px);
}

.clients-enter-active {
  transition: opacity 300ms ease 100ms, transform 300ms ease 100ms;
}

.clients-leave-active {
  transition: opacity 300ms ease, transform 300ms ease;
  position: absolute;
  width: 100%;
}

.clients-enter-from {
  opacity: 0;
  transform: translateY(12px);
}

.clients-leave-to {
  opacity: 0;
  transform: translateY(-12px);
}

@media (max-aspect-ratio: 4/3) {
  .message-content {
    min-height: 364px;
  }
}
</style>