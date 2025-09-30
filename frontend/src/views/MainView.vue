<!-- frontend/src/views/MainView.vue -->
<template>
  <div class="main-view">
    <div class="SettingsAccess" @click="handleSettingsClick"></div>

    <!-- AudioSourceView - Le plus bas -->
    <div class="content-container">
      <AudioSourceView :active-source="unifiedStore.currentSource" :plugin-state="unifiedStore.pluginState"
        :transitioning="unifiedStore.isTransitioning" :target-source="unifiedStore.systemState.target_source"
        :metadata="unifiedStore.metadata" :is-disconnecting="disconnectingStates[unifiedStore.currentSource]"
        @disconnect="handleDisconnect" />
    </div>

    <!-- Logo - Au-dessus d'AudioSource -->
    <Logo :position="logoPosition" :size="logoSize" :visible="logoVisible" :transition-mode="logoTransitionMode" />

    <!-- Modals -->
    <SnapcastModal :is-open="isSnapcastOpen" @close="isSnapcastOpen = false" />
    <EqualizerModal :is-open="isEqualizerOpen" @close="isEqualizerOpen = false" />
    <SettingsModal :is-open="isSettingsOpen" @close="isSettingsOpen = false" />

    <!-- Bottom Navigation -->
    <BottomNavigation @open-snapcast="isSnapcastOpen = true" @open-equalizer="isEqualizerOpen = true"
      @open-settings="isSettingsOpen = true" />
  </div>
</template>

<script setup>
import { computed, ref, watch, onMounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import AudioSourceView from '@/components/audio/AudioSourceView.vue';
import Logo from '@/components/ui/Logo.vue';
import BottomNavigation from '@/components/navigation/BottomNavigation.vue';
import SnapcastModal from '@/components/snapcast/SnapcastModal.vue';
import EqualizerModal from '@/components/equalizer/EqualizerModal.vue';
import SettingsModal from '@/components/settings/SettingsModal.vue';

const unifiedStore = useUnifiedAudioStore();

// États de déconnexion pour chaque plugin
const disconnectingStates = ref({
  bluetooth: false,
  roc: false,
  librespot: false
});

// États des modals
const isSnapcastOpen = ref(false);
const isEqualizerOpen = ref(false);
const isSettingsOpen = ref(false);

// États du logo
const logoPosition = ref('center');
const logoSize = ref('large');
const logoVisible = ref(true);
const logoTransitionMode = ref('normal');
const isInitialLoad = ref(true);

let logoTimeout = null;

// === COMPUTED POUR LES ÉTATS CIBLES ===
const isLibrespotFullScreen = computed(() => {
  const hasCompleteTrackInfo = !!(
    unifiedStore.pluginState === 'connected' &&
    unifiedStore.metadata?.title &&
    unifiedStore.metadata?.artist
  );

  return unifiedStore.currentSource === 'librespot' &&
    unifiedStore.pluginState === 'connected' &&
    hasCompleteTrackInfo &&
    !unifiedStore.isTransitioning;
});

const shouldShowLogo = computed(() => {
  return !isLibrespotFullScreen.value;
});

const targetPosition = computed(() => {
  if (unifiedStore.currentSource === 'none' && !unifiedStore.isTransitioning) {
    return 'center';
  }
  return 'top';
});

const targetSize = computed(() => {
  return targetPosition.value === 'center' ? 'large' : 'small';
});

// === LOGIQUE PRINCIPALE ===
watch([targetPosition, targetSize, shouldShowLogo], ([newPos, newSize, newVisible]) => {
  if (logoTimeout) {
    clearTimeout(logoTimeout);
    logoTimeout = null;
  }

  if (!newVisible && logoVisible.value) {
    logoTransitionMode.value = 'librespot-hide';

    if (!isInitialLoad.value) {
      logoVisible.value = false;
      return;
    }

    logoTimeout = setTimeout(() => {
      logoVisible.value = false;
    }, 900);
    return;
  }

  if (newVisible && !logoVisible.value) {
    logoTransitionMode.value = 'librespot-show';
    logoPosition.value = 'top';
    logoSize.value = 'small';
    logoVisible.value = true;
    isInitialLoad.value = false;
    return;
  }

  logoTransitionMode.value = 'normal';

  if (!isInitialLoad.value) {
    logoPosition.value = newPos;
    logoSize.value = newSize;
    logoVisible.value = newVisible;
    return;
  }

  logoTimeout = setTimeout(() => {
    logoPosition.value = newPos;
    logoSize.value = newSize;
    logoVisible.value = newVisible;
    isInitialLoad.value = false;
  }, 900);
}, { immediate: true });

onMounted(() => {
  logoPosition.value = 'center';
  logoSize.value = 'large';
  logoVisible.value = true;
  logoTransitionMode.value = 'normal';
});

// === GESTION DES ACTIONS ===
async function handleDisconnect() {
  const currentSource = unifiedStore.currentSource;
  if (!currentSource || currentSource === 'none') return;

  disconnectingStates.value[currentSource] = true;

  try {
    let response;

    switch (currentSource) {
      case 'bluetooth':
        response = await fetch('/api/bluetooth/disconnect', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' }
        });
        break;
      case 'roc':
        console.log('ROC disconnect not implemented');
        return;
      default:
        console.warn(`Disconnect not supported for ${currentSource}`);
        return;
    }

    if (response && response.ok) {
      const result = await response.json();
      if (result.status === 'success' || result.success) {
        console.log(`${currentSource} disconnected successfully`);
      } else {
        console.error(`Disconnect error: ${result.message || result.error}`);
      }
    }
  } catch (error) {
    console.error(`Error disconnecting ${currentSource}:`, error);
  } finally {
    setTimeout(() => {
      disconnectingStates.value[currentSource] = false;
    }, 900);
  }
}

// === ACCÈS CACHÉ À SETTINGS ===
const SETTINGS_CLICKS_REQUIRED = 5;
const CLICK_WINDOW_MS = 5000;
const settingsClicks = ref(0);

let clickWindowTimer = null;

function resetClickWindow() {
  settingsClicks.value = 0;
  if (clickWindowTimer) {
    clearTimeout(clickWindowTimer);
    clickWindowTimer = null;
  }
}

function handleSettingsClick() {
  if (settingsClicks.value === 0) {
    clickWindowTimer = setTimeout(() => {
      resetClickWindow();
    }, CLICK_WINDOW_MS);
  }

  settingsClicks.value += 1;

  if (settingsClicks.value >= SETTINGS_CLICKS_REQUIRED) {
    resetClickWindow();
    isSettingsOpen.value = true;
  }
}
</script>

<style scoped>
.main-view {
  background: var(--color-background);
  height: 100%;
  position: relative;
}

.content-container {
  width: 100%;
  height: 100%;
  position: relative;
  z-index: 1;
}

.SettingsAccess {
  position: absolute;
  top: 0;
  right: 0;
  width: 64px;
  height: 80px;
  z-index: 9999;
}
</style>