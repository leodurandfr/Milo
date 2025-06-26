<!-- frontend/src/views/RocView.vue - Version avec PluginStatus -->
<template>
  <div class="roc-view">
    <PluginStatus
      plugin-type="roc"
      :plugin-state="unifiedStore.pluginState"
      :device-name="cleanHostname"
    />
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import PluginStatus from '@/components/ui/PluginStatus.vue';
import axios from 'axios';

const unifiedStore = useUnifiedAudioStore();

// Nom d'hôte nettoyé pour l'affichage
const cleanHostname = computed(() => {
  const clientName = unifiedStore.metadata?.client_name;
  if (!clientName) return '';
  
  // Nettoyer le nom : enlever .local et remplacer - par espaces
  return clientName
    .replace('.local', '')           // Enlever .local
    .replace(/-/g, ' ');            // Remplacer - par espaces
});

onMounted(async () => {
  if (unifiedStore.currentSource === 'roc') {
    try {
      const response = await axios.get('/api/roc/status');
      const data = response.data;
      
      if (data.status === 'ok') {
        // Simuler un événement STATE_CHANGED complet pour ROC
        unifiedStore.updateState({
          data: {
            full_state: {
              active_source: 'roc',
              plugin_state: data.is_connected ? 'connected' : 'ready',
              transitioning: false,
              metadata: {
                client_name: data.client_name || '',
                connection_status: data.connection_status
              },
              error: null
            }
          }
        });
      }
    } catch (error) {
      console.error('Error fetching roc status:', error);
    }
  }
});
</script>

<style scoped>
.roc-view {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-05);
}
</style>