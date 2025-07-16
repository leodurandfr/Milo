<!-- MainView.vue - Phase 1 : PluginStatus centralisé -->
<template>
  <div class="main-view">
    <!-- Logo animé selon l'état -->
    <Logo 
      :position="logoPosition"
      :size="logoSize"
      :visible="logoVisible"
    />

    <!-- Conteneur stable qui reste toujours monté -->
    <div ref="containerRef" class="stable-container" :class="{ 'content-visible': !isInitialLogoDisplay }">

      <!-- LibrespotView (player quand connecté avec métadonnées) -->
      <LibrespotView 
        v-if="shouldShowLibrespotPlayer"
        :should-animate="shouldAnimateContent"
      />

      <!-- PluginStatus centralisé (toutes les autres situations) -->
      <div v-else-if="shouldShowPluginStatus" class="plugin-status-wrapper">
        <PluginStatus
          :plugin-type="currentPluginType"
          :plugin-state="currentPluginState"
          :device-name="currentDeviceName"
          :should-animate="shouldAnimateContent"
          :is-disconnecting="disconnectingStates[unifiedStore.currentSource]"
          @disconnect="handleDisconnect"
        />
      </div>

      <!-- Aucune source - pas de contenu, juste le logo centré -->
      <div v-else class="no-source">
        <!-- Le logo est géré par le composant Logo ci-dessus -->
      </div>

    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch, nextTick, onMounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import LibrespotView from './LibrespotView.vue';
import PluginStatus from '@/components/ui/PluginStatus.vue';
import Logo from '@/components/ui/Logo.vue';

const unifiedStore = useUnifiedAudioStore();
const containerRef = ref(null);

// États de déconnexion pour chaque plugin
const disconnectingStates = ref({
  bluetooth: false,
  roc: false,
  librespot: false
});

// État pour le logo initial (3 secondes)
const isInitialLogoDisplay = ref(true);
// État pour contrôler l'animation du contenu
const shouldAnimateContent = ref(false);

// === LOGIQUE D'AFFICHAGE CENTRALISÉE ===

// LibrespotView : Seulement quand connecté avec métadonnées complètes
const shouldShowLibrespotPlayer = computed(() => {
  return !unifiedStore.isTransitioning && 
         unifiedStore.displayedSource === 'librespot' &&
         unifiedStore.pluginState === 'connected' &&
         unifiedStore.metadata?.title &&
         unifiedStore.metadata?.artist;
});

// PluginStatus : Tous les autres cas (transitions, ready, bluetooth, roc)
const shouldShowPluginStatus = computed(() => {
  // Pendant une transition
  if (unifiedStore.isTransitioning) return true;
  
  // Sources qui utilisent toujours PluginStatus
  if (['bluetooth', 'roc'].includes(unifiedStore.displayedSource)) return true;
  
  // Librespot en mode ready ou sans métadonnées
  if (unifiedStore.displayedSource === 'librespot' && 
      (unifiedStore.pluginState === 'ready' || !shouldShowLibrespotPlayer.value)) {
    return true;
  }
  
  return false;
});

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
      return cleanHostname(metadata.client_name || '');
    case 'librespot':
      return ''; // Pas de device name pour Spotify
    default:
      return '';
  }
});

// === LOGIQUE DU LOGO (inchangée) ===

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
  if (shouldShowLibrespotPlayer.value) return false;
  return true;
});

// === FONCTIONS UTILITAIRES ===

function cleanHostname(hostname) {
  if (!hostname) return '';
  return hostname
    .replace('.local', '')
    .replace(/-/g, ' ');
}

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
        // ROC n'a pas de déconnexion explicite
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

// === ANIMATIONS (inchangées) ===

watch(() => [unifiedStore.isTransitioning, unifiedStore.currentSource], async ([isTransitioning, currentSource], [wasTransitioning, previousSource]) => {
  if (!containerRef.value) return;
  
  if ((isTransitioning !== wasTransitioning) || (currentSource !== previousSource)) {
    await animateContentChange();
  }
}, { flush: 'post' });

async function animateContentChange() {
  if (!containerRef.value) return;
  
  // Animation de sortie
  containerRef.value.style.transition = 'all var(--transition-spring)';
  containerRef.value.style.opacity = '0';
  containerRef.value.style.transform = 'translateY(calc(-1 * var(--space-06)))';
  
  await new Promise(resolve => setTimeout(resolve, 300));
  await nextTick();
  
  // Animation d'entrée
  containerRef.value.style.transition = 'none';
  containerRef.value.style.opacity = '0';
  containerRef.value.style.transform = 'translateY(var(--space-06))';
  containerRef.value.offsetHeight;
  
  containerRef.value.style.transition = 'all var(--transition-spring)';
  containerRef.value.style.opacity = '1';
  containerRef.value.style.transform = 'translateY(0)';
  
  setTimeout(() => {
    if (containerRef.value) {
      containerRef.value.style.transition = '';
      containerRef.value.style.transform = '';
    }
  }, 700);
}

onMounted(() => {
  setTimeout(async () => {
    isInitialLogoDisplay.value = false;
    await new Promise(resolve => setTimeout(resolve, 150));
    shouldAnimateContent.value = true;
  }, 1350);
});
</script>

<style scoped>
.main-view {
  background: var(--color-background);
  height: 100%;
  position: relative;
}

.stable-container {
  width: 100%;
  height: 100%;
  opacity: 1;
  transition: opacity var(--transition-spring);
}

.stable-container:not(.content-visible) {
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