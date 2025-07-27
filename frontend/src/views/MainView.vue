<!-- MainView.vue - Version avec ajustements animations -->
<template>
  <div class="main-view">
    <!-- Logo avec logique de centrage initial -->
    <Logo 
      :position="logoPosition"
      :size="logoSize"
      :visible="logoVisible"
    />

    <!-- Conteneur de contenu -->
    <div class="content-container">

      <!-- Transition centralisée -->
      <Transition name="main-content">
        
        <!-- LibrespotView -->
        <LibrespotView 
          v-if="shouldShowLibrespotView"
          key="librespot"
        />

        <!-- PluginStatus -->
        <div 
          v-else-if="shouldShowPluginStatus" 
          :key="pluginStatusKey"
          class="plugin-status-wrapper"
        >
          <PluginStatus
            :plugin-type="currentPluginType"
            :plugin-state="currentPluginState"
            :device-name="currentDeviceName"
            :is-disconnecting="disconnectingStates[unifiedStore.currentSource]"
            :should-animate="true"
            @disconnect="handleDisconnect"
          />
        </div>

      </Transition>

    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import LibrespotView from './LibrespotView.vue';
import PluginStatus from '@/components/ui/PluginStatus.vue';
import Logo from '@/components/ui/Logo.vue';

const unifiedStore = useUnifiedAudioStore();

// États de déconnexion pour chaque plugin
const disconnectingStates = ref({
  bluetooth: false,
  roc: false,
  librespot: false
});

// État pour affichage initial du logo (centré pendant 1000ms)
const showInitialLogo = ref(true);

// === LOGIQUE D'AFFICHAGE SIMPLE ===

// Condition pour avoir des métadonnées complètes
const hasCompleteTrackInfo = computed(() => {
  return !!(
    unifiedStore.pluginState === 'connected' &&
    unifiedStore.metadata?.title &&
    unifiedStore.metadata?.artist
  );
});

// LibrespotView : Afficher si on est sur librespot ET connecté (peu importe les métadonnées)
const shouldShowLibrespotView = computed(() => {
  if (showInitialLogo.value) return false; // Attendre fin logo initial
  return unifiedStore.displayedSource === 'librespot' && unifiedStore.pluginState === 'connected';
});

// PluginStatus : Tous les autres cas avec une source active ET après le délai initial
const shouldShowPluginStatus = computed(() => {
  if (showInitialLogo.value) return false; // Attendre fin logo initial
  
  // Transition en cours
  if (unifiedStore.isTransitioning) return true;
  
  // Sources bluetooth/roc : toujours PluginStatus
  if (['bluetooth', 'roc'].includes(unifiedStore.displayedSource)) return true;
  
  // Librespot en état ready (pas encore connecté) : PluginStatus
  if (unifiedStore.displayedSource === 'librespot' && unifiedStore.pluginState === 'ready') {
    return true;
  }
  
  return false;
});

// === PROPRIÉTÉS POUR PLUGINSTATUS ===

const currentPluginType = computed(() => {
  return unifiedStore.displayedSource;
});

const currentPluginState = computed(() => {
  if (unifiedStore.isTransitioning) return 'starting';
  return unifiedStore.pluginState;
});

const currentDeviceName = computed(() => {
  const metadata = unifiedStore.metadata || {};
  
  switch (unifiedStore.displayedSource) {
    case 'bluetooth':
      return metadata.device_name || '';
    case 'roc':
      return metadata.client_name || '';
    case 'librespot':
      return '';
    default:
      return '';
  }
});

// Clé qui change pour Ready/Connected ET changement de plugin
const pluginStatusKey = computed(() => {
  return `${currentPluginType.value}-${currentPluginState.value}-${!!currentDeviceName.value}`;
});

// === LOGIQUE DU LOGO AVEC CENTRAGE INITIAL ===

const logoPosition = computed(() => {
  // Logo centré pendant les 1000ms initiales
  if (showInitialLogo.value) return 'center';
  
  // Logo centré si aucune source ou aucun contenu affiché
  if (unifiedStore.currentSource === 'none' && !unifiedStore.isTransitioning) {
    return 'center';
  }
  
  // Logo en haut dans tous les autres cas
  return 'top';
});

const logoSize = computed(() => {
  return logoPosition.value === 'center' ? 'large' : 'small';
});

const logoVisible = computed(() => {
  // Logo visible pendant les 1000ms initiales
  if (showInitialLogo.value) return true;
  
  // Cacher le logo quand LibrespotView est affiché (il prend tout l'écran)
  if (shouldShowLibrespotView.value) return false;
  
  // Visible dans tous les autres cas
  return true;
});

// === LIFECYCLE ===
onMounted(() => {
  // Garder le logo centré pendant 1000ms au refresh
  setTimeout(() => {
    showInitialLogo.value = false;
  }, 1000);
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
    }, 1000);
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
}

/* === TRANSITIONS AVEC SPRING === */
.main-content-enter-active {
  transition: opacity 0.4s ease, transform var(--transition-spring);
  transition-delay: 0.05s;
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  will-change: opacity, transform;
}

.main-content-leave-active {
  transition: opacity 0.35s ease, transform var(--transition-spring);
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  will-change: opacity, transform;
  z-index: 1;
}

.main-content-enter-from {
  opacity: 0;
  transform: translateY(var(--space-06)) scale(0.98);
}

.main-content-leave-to {
  opacity: 0;
  transform: translateY(calc(-1 * var(--space-06))) scale(0.98);
}

.main-content-enter-to,
.main-content-leave-from {
  opacity: 1;
  transform: translateY(0) scale(1);
}

/* Entrant au-dessus du sortant */
.main-content-enter-active {
  z-index: 2;
}

.plugin-status-wrapper {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-05);
}
</style>