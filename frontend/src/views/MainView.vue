<!-- frontend/src/views/MainView.vue - Version finale simplifiée -->
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

      <!-- PluginStatus (transitions + états ready/connected) -->
      <div v-if="shouldShowPluginStatus" class="transition-state">
        <PluginStatus
          :plugin-type="pluginTypeToShow"
          :plugin-state="pluginStateToShow"
          :device-name="cleanDeviceName"
          :should-animate="shouldAnimateContent"
        />
      </div>

      <!-- LibrespotView (player quand connecté avec métadonnées) -->
      <LibrespotView 
        v-else-if="shouldShowLibrespotView"
        :should-animate="shouldAnimateContent"
      />

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

// États locaux qui retardent l'affichage pour figer le contenu pendant les animations
// Cela évite que le texte de PluginStatus change pendant la transition
const displayedIsTransitioning = ref(unifiedStore.isTransitioning);
const displayedCurrentSource = ref(unifiedStore.currentSource);
const displayedTargetPluginType = ref(unifiedStore.transitionTargetSource || unifiedStore.currentSource);

// État pour le logo initial (3 secondes)
const isInitialLogoDisplay = ref(true);
// État pour contrôler l'animation du contenu
const shouldAnimateContent = ref(false);

// === LOGIQUE D'AFFICHAGE ===

// LibrespotView s'affiche dès qu'on est sur librespot (il gère son affichage interne)
const shouldShowLibrespotView = computed(() => {
  return !displayedIsTransitioning.value && displayedCurrentSource.value === 'librespot';
});

// PluginStatus s'affiche pour les transitions et les sources bluetooth/roc
const shouldShowPluginStatus = computed(() => {
  // Pendant une transition
  if (displayedIsTransitioning.value) {
    return true;
  }
  
  // Pour bluetooth et roc (toujours PluginStatus)
  if (displayedCurrentSource.value === 'bluetooth' || displayedCurrentSource.value === 'roc') {
    return true;
  }
  
  // Pour librespot en mode ready (pas encore connecté)
  if (displayedCurrentSource.value === 'librespot' && unifiedStore.pluginState === 'ready') {
    return true;
  }
  
  return false;
});

// Déterminer le bon type de plugin à afficher (AVEC VARIABLES RETARDÉES)
const pluginTypeToShow = computed(() => {
  // Si on est en transition, on affiche toujours le plugin cible
  if (displayedIsTransitioning.value) {
    return displayedTargetPluginType.value === 'librespot' ? 'librespot' : displayedTargetPluginType.value;
  }
  
  // Sinon, on affiche le plugin actuel
  return displayedCurrentSource.value === 'librespot' ? 'librespot' : displayedCurrentSource.value;
});

// Déterminer l'état du plugin à afficher
const pluginStateToShow = computed(() => {
  if (displayedIsTransitioning.value) {
    return 'starting';
  }
  return unifiedStore.pluginState;
});

// === LOGIQUE DU LOGO ===

// Position du logo
const logoPosition = computed(() => {
  // Pendant les 3 premières secondes : toujours centré
  if (isInitialLogoDisplay.value) {
    return 'center';
  }
  
  // Centré uniquement quand aucune source n'est active
  if (unifiedStore.currentSource === 'none' && !unifiedStore.isTransitioning) {
    return 'center';
  }
  // Sinon en haut
  return 'top';
});

// Taille du logo
const logoSize = computed(() => {
  // Grand quand centré, petit quand en haut
  return logoPosition.value === 'center' ? 'large' : 'small';
});

// Visibilité du logo
const logoVisible = computed(() => {
  // Pendant les 3 premières secondes : toujours visible
  if (isInitialLogoDisplay.value) {
    return true;
  }
  
  // Cas spécial : Librespot connecté avec des infos de track
  if (unifiedStore.currentSource === 'librespot' && 
      unifiedStore.pluginState === 'connected' && 
      hasLibrespotTrackInfo.value) {
    return false;
  }
  
  // Visible dans tous les autres cas
  return true;
});

// Helper pour détecter si Librespot a des infos de track (pour le logo)
const hasLibrespotTrackInfo = computed(() => {
  return !!(
    unifiedStore.currentSource === 'librespot' &&
    unifiedStore.pluginState === 'connected' &&
    unifiedStore.metadata?.title &&
    unifiedStore.metadata?.artist
  );
});

// Helper pour nettoyer le nom du device
const cleanDeviceName = computed(() => {
  const deviceName = unifiedStore.metadata?.device_name || unifiedStore.metadata?.client_name || '';
  if (!deviceName) return '';
  
  // Nettoyer le nom : enlever .local et remplacer - par espaces
  return deviceName
    .replace('.local', '')           // Enlever .local
    .replace(/-/g, ' ');            // Remplacer - par espaces
});

// === ANIMATIONS SIMPLES ===

// Watcher pour animer les changements (AVEC MISE À JOUR RETARDÉE)
watch(() => [unifiedStore.isTransitioning, unifiedStore.currentSource], async ([isTransitioning, currentSource], [wasTransitioning, previousSource]) => {
  if (!containerRef.value) return;
  
  // Si il y a un changement significatif
  if ((isTransitioning !== wasTransitioning) || (currentSource !== previousSource)) {
    await animateContentChange();
  }
}, { flush: 'post' });

async function animateContentChange() {
  if (!containerRef.value) return;
  
  // 1. Animation de sortie (avec anciennes valeurs figées)
  containerRef.value.style.transition = 'all var(--transition-spring)';
  containerRef.value.style.opacity = '0';
  containerRef.value.style.transform = 'translateY(calc(-1 * var(--space-06)))';
  
  // 2. Attendre que l'élément soit invisible
  await new Promise(resolve => setTimeout(resolve, 300));
  
  // 3. Changer le contenu affiché APRÈS la disparition (évite le changement de texte pendant l'animation)
  displayedIsTransitioning.value = unifiedStore.isTransitioning;
  displayedCurrentSource.value = unifiedStore.currentSource;
  displayedTargetPluginType.value = unifiedStore.transitionTargetSource || unifiedStore.currentSource;
  
  // 4. Laisser Vue.js mettre à jour le DOM
  await nextTick();
  
  // 5. Préparer l'animation d'entrée
  containerRef.value.style.transition = 'none';
  containerRef.value.style.opacity = '0';
  containerRef.value.style.transform = 'translateY(var(--space-06))';
  
  // 6. Forcer le reflow
  containerRef.value.offsetHeight;
  
  // 7. Animation d'entrée
  containerRef.value.style.transition = 'all var(--transition-spring)';
  containerRef.value.style.opacity = '1';
  containerRef.value.style.transform = 'translateY(0)';
  
  // 8. Nettoyer les styles après l'animation
  setTimeout(() => {
    if (containerRef.value) {
      containerRef.value.style.transition = '';
      containerRef.value.style.transform = '';
    }
  }, 700);
}

// Gestion de l'affichage initial du logo (3 secondes)
onMounted(() => {
  setTimeout(async () => {
    // 1. Démarrer la transition du logo vers le haut
    isInitialLogoDisplay.value = false;
    
    // 2. Attendre que le logo termine son animation vers le haut
    await new Promise(resolve => setTimeout(resolve, 150));
    
    // 3. Maintenant déclencher l'animation des composants
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

/* Masquer le contenu pendant l'affichage initial du logo */
.stable-container:not(.content-visible) {
  opacity: 0;
  pointer-events: none;
}

.transition-state {
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