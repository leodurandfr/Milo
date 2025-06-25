<template>
  <div class="bluetooth-view">
    <div class="status-container">
      <div class="status-indicator" :class="connectionStatus"></div>
      <div class="status-text">{{ statusText }}</div>
    </div>
    
    <div v-if="connectedDevice" class="device-info">
      <div class="device-icon">🎵</div>
      <h3>{{ connectedDevice.name }}</h3>
      <p class="device-address">{{ connectedDevice.address }}</p>
      <div class="buttons-container">
        <button @click="disconnectDevice" class="disconnect-btn" :disabled="isDisconnecting">
          {{ isDisconnecting ? 'Déconnexion...' : 'Déconnecter' }}
        </button>
      </div>
    </div>
    
    <div v-else class="no-device">
      <div class="waiting-icon">🎧</div>
      <h3>En attente de connexion</h3>
      <p class="help-text">Connectez un appareil Bluetooth à "oakOS"</p>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import axios from 'axios';

const unifiedStore = useUnifiedAudioStore();
const isDisconnecting = ref(false);

const connectionStatus = computed(() => {
  return `status-${unifiedStore.pluginState}`;
});

const statusText = computed(() => {
  switch(unifiedStore.pluginState) {
    case 'inactive': return 'Inactif';
    case 'ready': return 'Prêt à connecter';
    case 'connected': return 'Connecté';
    case 'error': return 'Erreur';
    default: return 'Inconnu';
  }
});

const connectedDevice = computed(() => {
  if (unifiedStore.pluginState === 'connected' && unifiedStore.metadata) {
    return {
      name: unifiedStore.metadata.device_name || 'Appareil inconnu',
      address: unifiedStore.metadata.device_address
    };
  }
  return null;
});

async function disconnectDevice() {
  try {
    isDisconnecting.value = true;
    
    // Utiliser la route correcte pour la déconnexion
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
      // Utiliser la route correcte pour le statut
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
  max-width: 500px;
  margin: 0 auto;
  padding: 20px;
}

.status-container {
  display: flex;
  align-items: center;
  margin-bottom: 20px;
}

.status-indicator {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  margin-right: 10px;
}

.status-inactive { background-color: #888; }
.status-ready { background-color: #ffcc00; }
.status-connected { background-color: #4caf50; }
.status-error { background-color: #f44336; }

.device-info {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 20px;
  background-color: #f5f5f5;
  border-radius: 16px;
  margin-bottom: 20px;
}

.device-icon {
  font-size: 48px;
  margin-bottom: 20px;
}

.device-info h3 {
  margin: 0;
  font-size: 1.5rem;
  margin-bottom: 8px;
}

.device-address {
  font-size: 0.9rem;
  color: #666;
  margin-bottom: 20px;
}

.buttons-container {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
  max-width: 250px;
}

.disconnect-btn {
  background-color: #f44336;
  color: white;
  border: none;
  padding: 10px 20px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 1rem;
}

.disconnect-btn:hover { 
  background-color: #d32f2f; 
}

.disconnect-btn:disabled {
  background-color: #ccc;
  cursor: not-allowed;
}

.no-device {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 200px;
  color: #666;
  text-align: center;
}

.waiting-icon {
  font-size: 48px;
  margin-bottom: 20px;
}

.help-text {
  font-size: 0.9rem;
  color: #888;
}
</style>