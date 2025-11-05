<!-- frontend/src/views/MainView.vue -->
<template>
  <div class="main-view">
    <!-- Hotspot secret pour ouvrir Settings -->
    <div class="SettingsAccess" role="button" aria-label="Open settings" @click="handleSettingsClick"></div>

    <!-- Contenu principal -->
    <div class="content-container">
      <AudioSourceView
        :active-source="unifiedStore.systemState.active_source"
        :plugin-state="unifiedStore.systemState.plugin_state"
        :transitioning="unifiedStore.systemState.transitioning"
        :target-source="unifiedStore.systemState.target_source"
        :metadata="unifiedStore.systemState.metadata"
        :is-disconnecting="disconnectingStates[unifiedStore.systemState.active_source]"
        @disconnect="handleDisconnect"
      />
    </div>

    <!-- Logo -->
    <Logo
      :position="logoPosition"
      :size="logoSize"
      :visible="logoVisible"
      :transition-mode="logoTransitionMode"
    />

    <!-- Modal Settings -->
    <Modal :is-open="isSettingsOpen" @close="closeSettings" height-mode="auto">
      <SettingsModal @close="closeSettings" />
    </Modal>

    <!-- Radio Screensaver -->
    <RadioScreensaver :is-visible="isScreensaverVisible" @close="closeScreensaver" />
  </div>
</template>

<script setup>
import { computed, ref, watch, onMounted, onUnmounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

import AudioSourceView from '@/components/audio/AudioSourceView.vue';
import Logo from '@/components/ui/Logo.vue';

import Modal from '@/components/ui/Modal.vue';
import SettingsModal from '@/components/settings/SettingsModal.vue';
import RadioScreensaver from '@/components/audio/RadioScreensaver.vue';

const unifiedStore = useUnifiedAudioStore();

// === États de déconnexion pour chaque plugin ===
const disconnectingStates = ref({
  bluetooth: false,
  roc: false,
  librespot: false,
  radio: false
});

// === Radio Screensaver ===
const isScreensaverVisible = ref(false);
let inactivityTimer = null;
const SCREENSAVER_DELAY = 15000; // 15 secondes

// Vérifie si le screensaver doit être actif
const shouldMonitorInactivity = computed(() => {
  return unifiedStore.systemState.active_source === 'radio' &&
         unifiedStore.systemState.plugin_state === 'connected';
});

// Réinitialise le timer d'inactivité
function resetInactivityTimer() {
  clearInactivityTimer();

  if (!shouldMonitorInactivity.value || isScreensaverVisible.value) {
    return;
  }

  inactivityTimer = setTimeout(() => {
    isScreensaverVisible.value = true;
  }, SCREENSAVER_DELAY);
}

// Nettoie le timer d'inactivité
function clearInactivityTimer() {
  if (inactivityTimer) {
    clearTimeout(inactivityTimer);
    inactivityTimer = null;
  }
}

// Gestionnaire d'activité utilisateur
function handleUserActivity() {
  if (!isScreensaverVisible.value) {
    resetInactivityTimer();
  }
}

// Ajoute les listeners d'activité
function addActivityListeners() {
  document.addEventListener('pointermove', handleUserActivity, { passive: true });
  document.addEventListener('pointerdown', handleUserActivity, { passive: true });
  document.addEventListener('wheel', handleUserActivity, { passive: true });
  document.addEventListener('touchstart', handleUserActivity, { passive: true });
  document.addEventListener('touchmove', handleUserActivity, { passive: true });
}

// Retire les listeners d'activité
function removeActivityListeners() {
  document.removeEventListener('pointermove', handleUserActivity);
  document.removeEventListener('pointerdown', handleUserActivity);
  document.removeEventListener('wheel', handleUserActivity);
  document.removeEventListener('touchstart', handleUserActivity);
  document.removeEventListener('touchmove', handleUserActivity);
}

// Ferme le screensaver
function closeScreensaver() {
  isScreensaverVisible.value = false;
  resetInactivityTimer();
}

// Surveille l'état du plugin radio pour démarrer/arrêter le monitoring
watch(shouldMonitorInactivity, (shouldMonitor) => {
  if (shouldMonitor) {
    addActivityListeners();
    resetInactivityTimer();
  } else {
    removeActivityListeners();
    clearInactivityTimer();
    isScreensaverVisible.value = false;
  }
}, { immediate: true });

// === États du logo ===
const logoPosition = ref('center');  // 'center' | 'top'
const logoSize = ref('large');       // 'large' | 'small'
const logoVisible = ref(true);
const logoTransitionMode = ref('normal'); // 'normal' | 'librespot-hide' | 'librespot-show'
const isInitialLoad = ref(true); // Pour distinguer refresh vs transitions d'état

let logoTimeout = null;

// === COMPUTED POUR LES ÉTATS CIBLES ===
const isLibrespotFullScreen = computed(() => {
  const hasCompleteTrackInfo = !!(
    unifiedStore.systemState.plugin_state === 'connected' &&
    unifiedStore.systemState.metadata?.title &&
    unifiedStore.systemState.metadata?.artist
  );

  return unifiedStore.systemState.active_source === 'librespot' &&
    unifiedStore.systemState.plugin_state === 'connected' &&
    hasCompleteTrackInfo &&
    !unifiedStore.systemState.transitioning;
});

const shouldShowLogo = computed(() => {
  // Masquer le logo pour librespot ET radio
  if (unifiedStore.systemState.active_source === 'radio') {
    return false;
  }
  return !isLibrespotFullScreen.value;
});

const targetPosition = computed(() => {
  if (unifiedStore.systemState.active_source === 'none' && !unifiedStore.systemState.transitioning) {
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

  // CAS 1 & 2: Logo visible → caché pour librespot
  if (!newVisible && logoVisible.value) {
    logoTransitionMode.value = 'librespot-hide';

    // Transition immédiate si ce n’est pas le chargement initial
    if (!isInitialLoad.value) {
      logoVisible.value = false;
      return;
    }

    // Délai seulement au chargement initial (refresh)
    logoTimeout = setTimeout(() => {
      logoVisible.value = false;
    }, 900);
    return;
  }

  // CAS 3: Logo caché → visible (retour de librespot)
  if (newVisible && !logoVisible.value) {
    logoTransitionMode.value = 'librespot-show';
    logoPosition.value = 'top';
    logoSize.value = 'small';
    logoVisible.value = true;
    isInitialLoad.value = false;
    return;
  }

  // CAS 1 & 2: Changements normaux
  logoTransitionMode.value = 'normal';

  // Transition immédiate si ce n’est pas le chargement initial
  if (!isInitialLoad.value) {
    logoPosition.value = newPos;
    logoSize.value = newSize;
    logoVisible.value = newVisible;
    return;
  }

  // Délai seulement au chargement initial (refresh)
  logoTimeout = setTimeout(() => {
    logoPosition.value = newPos;
    logoSize.value = newSize;
    logoVisible.value = newVisible;
    isInitialLoad.value = false;
  }, 900);
}, { immediate: true });

// Initialisation au montage
onMounted(() => {
  // Toujours démarrer big+center, même pour librespot connected
  logoPosition.value = 'center';
  logoSize.value = 'large';
  logoVisible.value = true;
  logoTransitionMode.value = 'normal';
});

// === GESTION DES ACTIONS ===
async function handleDisconnect() {
  const currentSource = unifiedStore.systemState.active_source;
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

/* =========================
   Accès à Settings (tap secret)
   ========================= */
const isSettingsOpen = ref(false);

function openSettings() {
  isSettingsOpen.value = true;
}
function closeSettings() {
  isSettingsOpen.value = false;
}

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
    openSettings(); // ✅ Ouvre la modal au lieu d'aller sur /settings
  }
}

onUnmounted(() => {
  // Nettoyage si on quitte la vue
  if (clickWindowTimer) clearTimeout(clickWindowTimer);
  if (logoTimeout) clearTimeout(logoTimeout);

  // Nettoyage du screensaver
  removeActivityListeners();
  clearInactivityTimer();
});
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