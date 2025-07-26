<!-- MainView.vue - Version OPTIM simplifiée -->
<template>
  <div class="main-view">
    <!-- Logo animé selon l'état -->
    <Logo 
      :position="logoPosition"
      :size="logoSize"
      :visible="logoVisible"
    />

    <!-- Conteneur de contenu simple -->
    <div class="content-container" :class="{ 'content-visible': !isInitialLogoDisplay }">

      <!-- LibrespotView avec animation simplifiée -->
      <LibrespotView 
        v-if="shouldShowLibrespotView"
        :should-animate="shouldAnimateLibrespot"
      />

      <!-- PluginStatus autonome - TOUJOURS présent pour permettre l'animation -->
      <div v-if="shouldShowPluginStatus" class="plugin-status-wrapper">
        <PluginStatus
          :plugin-type="currentPluginType"
          :plugin-state="currentPluginState"
          :device-name="currentDeviceName"
          :is-disconnecting="disconnectingStates[unifiedStore.currentSource]"
          :should-animate="pluginStatusShouldAnimate"
          @disconnect="handleDisconnect"
        />
      </div>

      <!-- Aucune source - pas de contenu, juste le logo centré -->
      <div v-else-if="unifiedStore.currentSource === 'none' && !unifiedStore.isTransitioning" class="no-source">
        <!-- Le logo est géré par le composant Logo ci-dessus -->
      </div>

    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted, watch } from 'vue';
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

// État pour le logo initial
const isInitialLogoDisplay = ref(true);
const shouldAnimateContent = ref(false);

// OPTIM : Animation simplifiée
const shouldAnimateLibrespot = ref(false);

// AJOUT : Animation PluginStatus selon le contexte
const pluginStatusShouldAnimate = computed(() => {
  // Si PluginStatus ne doit pas être affiché, pas d'animation
  if (!shouldShowPluginStatus.value) return false;
  
  // Toujours attendre que le contenu soit visible (après le logo)
  if (!shouldAnimateContent.value) {
    console.log('🔌 PluginStatus waiting for content to be visible');
    return false;
  }
  
  console.log('🔌 PluginStatus should animate now');
  return true;
});

// === LOGIQUE D'AFFICHAGE CENTRALISÉE ===

// Condition pour avoir des métadonnées complètes
const hasCompleteTrackInfo = computed(() => {
  return !!(
    unifiedStore.pluginState === 'connected' &&
    unifiedStore.metadata?.title &&
    unifiedStore.metadata?.artist
  );
});

// LibrespotView : Afficher si on a les métadonnées
const shouldShowLibrespotView = computed(() => {
  return unifiedStore.displayedSource === 'librespot' && hasCompleteTrackInfo.value;
});

// PluginStatus : Tous les autres cas
const shouldShowPluginStatus = computed(() => {
  if (unifiedStore.isTransitioning) return true;
  if (['bluetooth', 'roc'].includes(unifiedStore.displayedSource)) return true;
  if (unifiedStore.displayedSource === 'librespot' && 
      (unifiedStore.pluginState === 'ready' || !hasCompleteTrackInfo.value)) {
    return true;
  }
  return false;
});

// === GESTION ANIMATION LIBRESPOT OPTIM ===

watch([shouldShowLibrespotView, shouldAnimateContent], 
  ([showView, canAnimate], [prevShowView]) => {
    
    console.log('🎵 LibrespotView conditions:', { showView, canAnimate, prevShowView });

    // Disparition : était affiché, ne l'est plus
    if (prevShowView && !showView) {
      console.log('🎵 LibrespotView should exit');
      shouldAnimateLibrespot.value = false;
      return;
    }

    // Apparition : doit s'afficher ET peut animer
    if (showView && canAnimate) {
      console.log('🎵 LibrespotView should enter');
      shouldAnimateLibrespot.value = true;
      return;
    }

    // Attente : doit s'afficher mais pas encore prêt
    if (showView && !canAnimate) {
      console.log('🎵 LibrespotView waiting...');
      shouldAnimateLibrespot.value = false;
    }
    
  }, { immediate: true }
);

// === PROPRIÉTÉS CALCULÉES POUR PLUGINSTATUS ===

const currentPluginType = computed(() => {
  if (unifiedStore.isTransitioning && unifiedStore.displayedSource !== 'none') {
    return unifiedStore.displayedSource;
  }
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

// === LOGIQUE DU LOGO ===

const logoPosition = computed(() => {
  if (isInitialLogoDisplay.value) return 'center';
  if (unifiedStore.currentSource === 'none' && !unifiedStore.isTransitioning) return 'center';
  return 'top';
});

const logoSize = computed(() => {
  return logoPosition.value === 'center' ? 'large' : 'small';
});

const logoVisible = computed(() => {
  if (isInitialLogoDisplay.value) return true;
  // Cacher le logo dès que LibrespotView s'affiche
  if (shouldShowLibrespotView.value && shouldAnimateLibrespot.value) return false;
  return true;
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

// === GESTION DU LOGO INITIAL ===

onMounted(() => {
  setTimeout(async () => {
    isInitialLogoDisplay.value = false;
    await new Promise(resolve => setTimeout(resolve, 150));
    shouldAnimateContent.value = true;
  }, 350);
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
  opacity: 1;
  transition: opacity var(--transition-spring);
}

/* Masquer le contenu pendant l'affichage initial du logo */
.content-container:not(.content-visible) {
  opacity: 0;
  pointer-events: none;
}

.plugin-status-wrapper {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-05);
}

.no-source {
  width: 100%;
  height: 100%;
}
</style>