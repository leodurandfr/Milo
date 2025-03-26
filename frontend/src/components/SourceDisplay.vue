<template>
  <div class="source-display">
    <!-- Chaque source a son propre composant d'affichage -->
    <component :is="currentSourceComponent" v-if="currentSourceComponent" />
    
    <!-- Message d'erreur si aucune source n'est trouvée -->
    <div v-else class="no-source-error">
      <h2>Source non disponible</h2>
      <p>La source audio "{{ audioStore.currentState }}" n'est pas disponible ou n'est pas encore implémentée.</p>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useAudioStore } from '@/stores/index';

// Importer les composants d'affichage spécifiques à chaque source
// Note: Ces imports peuvent être dynamiques pour optimiser les performances si nécessaire
import LibrespotDisplay from '@/components/sources/librespot/LibrespotDisplay.vue';
import SnapclientDisplay from '@/components/sources/snapclient/SnapclientDisplay.vue';

const audioStore = useAudioStore();

// Mapper chaque source à son composant d'affichage
const sourceComponents = {
  'librespot': LibrespotDisplay,
  'macos': SnapclientDisplay,
  // Autres sources à ajouter au fur et à mesure de leur implémentation
  // 'bluetooth': BluetoothDisplay,
  // 'webradio': WebRadioDisplay,
};

// Déterminer le composant à afficher en fonction de la source actuelle
const currentSourceComponent = computed(() => {
  const source = audioStore.currentState;
  return sourceComponents[source] || null;
});
</script>

<style scoped>
.source-display {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: auto;
  background-color: #ffffff;
  border-radius: 8px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05);
}

.no-source-error {
  padding: 1rem;
  text-align: center;
}
</style>