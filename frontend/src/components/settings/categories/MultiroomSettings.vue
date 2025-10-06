<!-- frontend/src/components/settings/categories/MultiroomSettings.vue -->
<template>
  <div class="settings-container">
    <!-- Enceintes multiroom -->
    <section class="settings-section">
      <div class="multiroom-group">
        <h2 class="heading-2 text-body">{{ t('Enceintes multiroom') }}</h2>

        <div v-if="loadingClients" class="loading-state">
          <p class="text-mono">{{ t('Chargement des enceintes...') }}</p>
        </div>

        <div v-else-if="sortedSnapcastClients.length === 0" class="no-clients-state">
          <p class="text-mono">{{ t('Aucune enceinte connectée') }}</p>
        </div>

        <div v-else class="clients-list" :style="clientsGridStyle">
          <div v-for="(client, index) in sortedSnapcastClients" :key="client.id" class="client-config-item"
            :style="getClientGridStyle(index)">
            <div class="client-info-wrapper">
              <span class="client-hostname text-mono">{{ client.host }}</span>
              <input type="text" v-model="clientNames[client.id]" :placeholder="client.host"
                class="client-name-input text-body" maxlength="50" @blur="updateClientName(client.id)"
                @keyup.enter="updateClientName(client.id)">
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Presets audio -->
    <section class="settings-section">
      <div class="multiroom-group">
        <h2 class="heading-2 text-body">{{ t('Pré-réglages') }}</h2>
        <div class="presets-buttons">
          <Button v-for="preset in audioPresets" :key="preset.id" variant="toggle" :active="isPresetActive(preset)"
            :disabled="applyingServerConfig" @click="applyPreset(preset)">
            {{ preset.name }}
          </Button>
        </div>
      </div>
    </section>

    <!-- Paramètres avancés -->
    <section class="settings-section">
      <div class="multiroom-group">
        <h2 class="heading-2 text-body">{{ t('Paramètres avancés') }}</h2>

        <div class="form-group">
          <label class="text-mono">{{ t('Buffer global (ms)') }}</label>
          <RangeSlider v-model="serverConfig.buffer" :min="100" :max="2000" :step="50" value-unit="ms" />
        </div>

        <div class="form-group">
          <label class="text-mono">{{ t('Taille des chunks (ms)') }}</label>
          <RangeSlider v-model="serverConfig.chunk_ms" :min="10" :max="100" :step="5" value-unit="ms" />
        </div>

        <div class="form-group">
          <label class="text-mono">{{ t('Codec audio') }}</label>
          <div class="codec-buttons">
            <Button variant="toggle" :active="serverConfig.codec === 'opus'" @click="selectCodec('opus')">
              Opus
            </Button>
            <Button variant="toggle" :active="serverConfig.codec === 'flac'" @click="selectCodec('flac')">
              FLAC
            </Button>
            <Button variant="toggle" :active="serverConfig.codec === 'pcm'" @click="selectCodec('pcm')">
              PCM
            </Button>
          </div>
        </div>
      </div>
    </section>

    <Button v-if="hasServerConfigChanges" variant="primary" class="apply-button-sticky"
      :disabled="loadingServerConfig || applyingServerConfig" @click="applyServerConfig">
      {{ applyingServerConfig ? t('Redémarrage du multiroom en cours...') : t('Appliquer') }}
    </Button>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useSettingsStore } from '@/stores/settingsStore';
import axios from 'axios';
import Button from '@/components/ui/Button.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';

const { t } = useI18n();
const { on } = useWebSocket();
const settingsStore = useSettingsStore();

// Multiroom - Clients (utilise le store)
const loadingClients = ref(false);
const clientNames = ref({});

// Clients triés avec "milo" en premier
const sortedSnapcastClients = computed(() => {
  const clients = [...settingsStore.snapcastClients];
  return clients.sort((a, b) => {
    if (a.host === 'milo') return -1;
    if (b.host === 'milo') return 1;
    return 0;
  });
});

// Multiroom - Server config (utilise le store)
const serverConfig = ref({
  buffer: 1000,
  codec: 'flac',
  chunk_ms: 20,
  sampleformat: '48000:16:2'
});
const originalServerConfig = ref({});
const loadingServerConfig = ref(false);
const applyingServerConfig = ref(false);

const audioPresets = computed(() => [
  {
    id: 'reactivity',
    name: t('Réactivité'),
    config: { buffer: 150, codec: 'opus', chunk_ms: 10 }
  },
  {
    id: 'balanced',
    name: t('Équilibré'),
    config: { buffer: 1000, codec: 'opus', chunk_ms: 20 }
  },
  {
    id: 'quality',
    name: t('Qualité optimale'),
    config: { buffer: 1500, codec: 'flac', chunk_ms: 40 }
  }
]);

const hasServerConfigChanges = computed(() => {
  return JSON.stringify(serverConfig.value) !== JSON.stringify(originalServerConfig.value);
});

// Style dynamique pour la grille
const clientsGridStyle = computed(() => {
  const count = sortedSnapcastClients.value.length;

  if (count <= 1) {
    return { display: 'flex', flexDirection: 'column' };
  }

  if (count <= 3) {
    return {
      display: 'flex',
      gap: 'var(--space-02)'
    };
  }

  if (count === 4) {
    return {
      display: 'grid',
      gridTemplateColumns: 'repeat(2, 1fr)',
      gap: 'var(--space-02)'
    };
  }

  // 5+ items : grid 3 colonnes
  return {
    display: 'grid',
    gridTemplateColumns: 'repeat(3, 1fr)',
    gap: 'var(--space-02)'
  };
});

// Position grid pour chaque item (ligne courte en haut)
const getClientGridStyle = (index) => {
  const count = sortedSnapcastClients.value.length;

  // Pas de positionnement spécifique pour 1-4 items
  if (count <= 4) return {};

  const remainder = count % 3;

  // Si divisible par 3, pas besoin de réorganiser
  if (remainder === 0) return {};

  // Première ligne : remainder items
  if (index < remainder) {
    return {
      gridRow: 1,
      gridColumn: index + 1
    };
  }

  // Lignes suivantes : groupes de 3
  const adjustedIndex = index - remainder;
  const row = Math.floor(adjustedIndex / 3) + 2;
  const col = (adjustedIndex % 3) + 1;

  return {
    gridRow: row,
    gridColumn: col
  };
};

function isPresetActive(preset) {
  const current = serverConfig.value;
  const presetConfig = preset.config;
  return current.buffer === presetConfig.buffer &&
    current.codec === presetConfig.codec &&
    current.chunk_ms === presetConfig.chunk_ms;
}

// === MULTIROOM - CLIENTS ===

async function loadSnapcastClients() {
  // Les clients sont déjà dans le store, on initialise juste les noms
  clientNames.value = {};
  settingsStore.snapcastClients.forEach(client => {
    clientNames.value[client.id] = client.name || client.host;
  });
}

async function updateClientName(clientId) {
  const newName = clientNames.value[clientId]?.trim();
  if (!newName) return;

  try {
    const response = await axios.post(`/api/routing/snapcast/client/${clientId}/name`, {
      name: newName
    });

    if (response.data.status === 'success') {
      console.log(`Client ${clientId} name updated to: ${newName}`);
    }
  } catch (error) {
    console.error('Error updating client name:', error);
  }
}

// === MULTIROOM - SERVER CONFIG ===

function applyPreset(preset) {
  serverConfig.value.buffer = preset.config.buffer;
  serverConfig.value.codec = preset.config.codec;
  serverConfig.value.chunk_ms = preset.config.chunk_ms;
}

function selectCodec(codecName) {
  serverConfig.value.codec = codecName;
}

async function loadServerConfig() {
  // La config serveur est déjà dans le store
  serverConfig.value = { ...settingsStore.snapcastServerConfig };
  originalServerConfig.value = JSON.parse(JSON.stringify(serverConfig.value));
}

async function applyServerConfig() {
  if (!hasServerConfigChanges.value || applyingServerConfig.value) return;

  applyingServerConfig.value = true;
  try {
    const response = await axios.post('/api/routing/snapcast/server/config', {
      config: serverConfig.value
    });

    if (response.data.status === 'success') {
      originalServerConfig.value = JSON.parse(JSON.stringify(serverConfig.value));
      console.log('Server config applied successfully');
    }
  } catch (error) {
    console.error('Error applying server config:', error);
  } finally {
    applyingServerConfig.value = false;
  }
}

// WebSocket listener
const handleClientNameChanged = (msg) => {
  const { client_id, name } = msg.data;
  if (clientNames.value[client_id] !== undefined) {
    clientNames.value[client_id] = name;
  }
  // Mettre à jour le store
  settingsStore.updateSnapcastClientName(client_id, name);
};

onMounted(() => {
  // Les données sont déjà pré-chargées dans le store
  loadSnapcastClients();
  loadServerConfig();

  on('snapcast', 'client_name_changed', handleClientNameChanged);
});
</script>

<style scoped>
.settings-container {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  padding: var(--space-06) var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.multiroom-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.loading-state,
.no-clients-state {
  text-align: center;
  padding: var(--space-04);
  color: var(--color-text-secondary);
}

.clients-list {
  /* Le style est appliqué dynamiquement via clientsGridStyle */
}

.client-config-item {
  background: var(--color-background-strong);
  border-radius: var(--radius-04);
  padding: var(--space-03);
  width: 100%;
}

.client-info-wrapper {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.client-hostname {
  color: var(--color-text-secondary);
  font-size: var(--font-size-small);
}

.client-name-input {
  padding: var(--space-02) var(--space-03);
  border: 2px solid var(--color-background-glass);
  border-radius: var(--radius-03);
  background: var(--color-background-neutral);
  color: var(--color-text);
  transition: border-color var(--transition-fast);
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
}

.client-name-input:focus {
  outline: none;
  border-color: var(--color-brand);
}

.client-name-input::placeholder {
  color: var(--color-text-light);
}

.presets-buttons {
  display: flex;
  gap: var(--space-02);
}

.presets-buttons .btn {
  flex: 1;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.form-group label {
  color: var(--color-text-secondary);
}

.codec-buttons {
  display: flex;
  gap: var(--space-02);
}

.codec-buttons .btn {
  flex: 1;
}

.apply-button-sticky {
  position: sticky;
  bottom: 0;
  width: 100%;
  z-index: 10;
}


/* Responsive */
@media (max-aspect-ratio: 4/3) {

  .codec-buttons,
  .presets-buttons {
    flex-direction: column;
  }

  .clients-list {
    display: flex !important;
    flex-direction: column !important;
    gap: var(--space-02) !important;
  }

  .client-config-item {
    grid-row: unset !important;
    grid-column: unset !important;
  }
}
</style>
