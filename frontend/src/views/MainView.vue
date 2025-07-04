<!-- frontend/src/views/MainView.vue - Version avec conteneur stable -->
<template>
  <div class="main-view">
    <!-- Conteneur stable qui reste toujours monté -->
    <div ref="containerRef" class="stable-container">
      
      <!-- État de transition -->
      <div v-if="displayedIsTransitioning" class="transition-state">
        <PluginStatus
          :plugin-type="displayedTargetPluginType"
          plugin-state="starting"
          device-name=""
        />
      </div>

      <!-- Contenu normal -->
      <component v-else-if="currentComponent" :is="currentComponent" />

      <!-- Aucune source -->
      <div v-else class="no-source">
        <h2>oakOS</h2>
      </div>
      
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch, nextTick } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import LibrespotView from './LibrespotView.vue';
import BluetoothView from './BluetoothView.vue';
import RocView from './RocView.vue';
import PluginStatus from '@/components/ui/PluginStatus.vue';

const unifiedStore = useUnifiedAudioStore();
const containerRef = ref(null);

// États locaux qui retardent l'affichage
const displayedIsTransitioning = ref(unifiedStore.isTransitioning);
const displayedCurrentSource = ref(unifiedStore.currentSource);
const displayedTargetPluginType = ref(unifiedStore.transitionTargetSource || unifiedStore.currentSource);

// Composant dynamique selon la source affichée (retardée)
const currentComponent = computed(() => {
  switch (displayedCurrentSource.value) {
    case 'librespot': return LibrespotView;
    case 'bluetooth': return BluetoothView;
    case 'roc': return RocView;
    default: return null;
  }
});

// Watcher pour animer les changements
watch(() => [unifiedStore.isTransitioning, unifiedStore.currentSource], async ([isTransitioning, currentSource], [wasTransitioning, previousSource]) => {
  if (!containerRef.value) return;
  
  // Si on commence une transition ou si la source change
  if ((isTransitioning !== wasTransitioning) || (currentSource !== previousSource)) {
    await animateContentChange();
  }
}, { flush: 'post' });

async function animateContentChange() {
  if (!containerRef.value) return;
  
  // 1. Animation de sortie
  containerRef.value.style.transition = 'all var(--transition-spring)';
  containerRef.value.style.opacity = '0';
  containerRef.value.style.transform = 'translateY(calc(-1 * var(--space-06)))';
  
  // 2. Attendre que l'élément soit invisible
  await new Promise(resolve => setTimeout(resolve, 300));
  
  // 3. MAINTENANT changer le contenu affiché (retardé)
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
}
</script>

<style scoped>
.main-view {
    background: var(--color-background);
     height: 100%;
}

.stable-container {
  width: 100%;
  height: 100%;
}

.transition-state {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-05);
}

</style>