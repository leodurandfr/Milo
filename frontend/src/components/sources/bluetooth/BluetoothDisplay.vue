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
        <button @click="disconnectDevice" class="disconnect-btn">
          Déconnecter
        </button>
        <button @click="restartAudio" class="restart-btn">
          Redémarrer l'audio
        </button>
        <button @click="startDirectAudio" class="emergency-btn">
          Démarrer Audio (Urgence)
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
import { computed, onMounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import axios from 'axios';

const unifiedStore = useUnifiedAudioStore();

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
    await axios.post('/bluetooth/control/bluetooth', {
      command: 'disconnect',
      data: {}
    });
  } catch (error) {
    console.error('Erreur lors de la déconnexion:', error);
  }
}

async function restartAudio() {
  try {
    await axios.post('/bluetooth/control/bluetooth', {
      command: 'restart_audio',
      data: {}
    });
  } catch (error) {
    console.error('Erreur lors du redémarrage audio:', error);
  }
}

async function startDirectAudio() {
  try {
    await axios.post('/bluetooth/control/bluetooth', {
      command: 'start_direct_audio',
      data: {}
    });
  } catch (error) {
    console.error('Erreur lors du démarrage direct audio:', error);
  }
}

onMounted(async () => {
  if (unifiedStore.currentSource === 'bluetooth') {
    try {
      const response = await axios.get('/bluetooth/status');
      if (response.data.status === 'ok') {
        // Simuler un événement STATE_CHANGED complet
        unifiedStore.updateState({
          data: {
            full_state: {
              active_source: 'bluetooth',
              plugin_state: response.data.is_active ? 'connected' : 'ready',
              transitioning: false,
              metadata: {
                device_name: response.data.device_name,
                device_address: response.data.device_address,
                device_connected: response.data.device_connected
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
  border-radius: 8px;
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

.restart-btn {
  background-color: #4285f4;
  color: white;
  border: none;
  padding: 10px 20px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 1rem;
}

.emergency-btn {
  background-color: #ff9800;
  color: white;
  border: none;
  padding: 10px 20px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 1rem;
}

.disconnect-btn:hover { background-color: #d32f2f; }
.restart-btn:hover { background-color: #3367d6; }
.emergency-btn:hover { background-color: #f57c00; }

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