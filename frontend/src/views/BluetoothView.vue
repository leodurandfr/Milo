<!-- frontend/src/views/BluetoothView.vue - Version avec PluginStatus -->
<template>
  <div class="bluetooth-view">
    <PluginStatus
      plugin-type="bluetooth"
      :plugin-state="unifiedStore.pluginState"
      :device-name="connectedDeviceName"
      :is-disconnecting="isDisconnecting"
      @disconnect="disconnectDevice"
    />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import PluginStatus from '@/components/ui/PluginStatus.vue';
import axios from 'axios';

const unifiedStore = useUnifiedAudioStore();
const isDisconnecting = ref(false);

// Nom de l'appareil connecté
const connectedDeviceName = computed(() => {
  if (unifiedStore.pluginState === 'connected' && unifiedStore.metadata) {
    return unifiedStore.metadata.device_name || 'Appareil inconnu';
  }
  return '';
});

async function disconnectDevice() {
  if (isDisconnecting.value) return;
  
  try {
    isDisconnecting.value = true;
    
    const response = await fetch('/api/bluetooth/disconnect', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      }
    });
    
    const result = await response.json();
    
    if (result.status === 'success' || result.success) {
      console.log('Appareil déconnecté avec succès');
    } else {
      console.error(`Erreur: ${result.message || result.error || 'Échec de la déconnexion'}`);
    }
  } catch (error) {
    console.error('Erreur lors de la déconnexion:', error);
  } finally {
    isDisconnecting.value = false;
  }
}

onMounted(async () => {
  if (unifiedStore.currentSource === 'bluetooth') {
    try {
      const response = await axios.get('/api/bluetooth/status');
      const data = response.data;
      
      if (data.status === 'ok') {
        // Simuler un événement STATE_CHANGED complet
        unifiedStore.updateState({
          data: {
            full_state: {
              active_source: 'bluetooth',
              plugin_state: data.is_active ? 'connected' : 'ready',
              transitioning: false,
              metadata: {
                device_name: data.device_name,
                device_address: data.device_address,
                device_connected: data.device_connected
              },
              error: null
            }
          }
        });
      }
    } catch (error) {
      console.error('Error fetching bluetooth status:', error);
    }
  }
});
</script>

<style scoped>
.bluetooth-view {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-05);
}
</style>