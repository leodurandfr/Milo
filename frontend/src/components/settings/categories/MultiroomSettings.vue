<!-- frontend/src/components/settings/categories/MultiroomSettings.vue -->
<template>
  <div class="multiroom-settings">
    <!-- Contenu principal avec hauteur animée -->
    <div class="content-container" :style="{ height: containerHeight }">
      <div class="content-wrapper" ref="contentWrapperRef" :class="{ 'with-background': !isMultiroomActive }">
        <!-- MESSAGE : Multiroom désactivé -->
        <Transition name="message">
          <div v-if="!isMultiroomActive" key="message" class="message-content">
            <Icon name="multiroom" :size="96" color="var(--color-background-glass)" />
            <p class="text-mono">{{ t("multiroom.disabled") }}</p>
          </div>
        </Transition>

        <!-- SETTINGS : Sections visibles uniquement si multiroom activé -->
        <Transition name="settings">
          <div v-if="isMultiroomActive" key="settings" class="settings-container">
        <!-- Enceintes multiroom -->
        <section class="settings-section">
      <div class="multiroom-group">
        <h2 class="heading-2 text-body">{{ t('multiroom.speakers') }}</h2>

        <div v-if="snapcastStore.isLoading" class="loading-state">
          <p class="text-mono">{{ t('multiroom.loadingSpeakers') }}</p>
        </div>

        <div v-else-if="sortedSnapcastClients.length === 0" class="no-clients-state">
          <p class="text-mono">{{ t('multiroom.noSpeakers') }}</p>
        </div>

        <div v-else class="clients-list" :style="clientsGridStyle">
          <div v-for="(client, index) in sortedSnapcastClients" :key="client.id" class="client-config-item"
            :style="getClientGridStyle(index)">
            <div class="client-info-wrapper">
              <span class="client-hostname text-mono">{{ client.host }}</span>
              <InputText v-model="clientNames[client.id]" :placeholder="client.host"
                input-class="client-name-input text-body" :maxlength="50"
                @blur="handleInputBlur(client.id)" />
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Presets audio -->
    <section class="settings-section">
      <div class="multiroom-group">
        <h2 class="heading-2 text-body">{{ t('multiroomSettings.presets') }}</h2>
        <div class="presets-buttons">
          <Button v-for="preset in audioPresets" :key="preset.id" variant="toggle" :active="isPresetActive(preset)"
            :disabled="snapcastStore.isApplyingServerConfig" @click="applyPreset(preset)">
            {{ preset.name }}
          </Button>
        </div>
      </div>
    </section>

    <!-- Paramètres avancés -->
    <section class="settings-section">
      <div class="multiroom-group">
        <h2 class="heading-2 text-body">{{ t('multiroomSettings.advanced') }}</h2>

        <div class="form-group">
          <label class="text-mono">{{ t('multiroomSettings.globalBuffer') }}</label>
          <RangeSlider v-model="snapcastStore.serverConfig.buffer" :min="100" :max="2000" :step="50" value-unit="ms" />
        </div>

        <div class="form-group">
          <label class="text-mono">{{ t('multiroomSettings.chunkSize') }}</label>
          <RangeSlider v-model="snapcastStore.serverConfig.chunk_ms" :min="10" :max="100" :step="5" value-unit="ms" />
        </div>

        <div class="form-group">
          <label class="text-mono">{{ t('multiroomSettings.codec') }}</label>
          <div class="codec-buttons">
            <Button variant="toggle" :active="snapcastStore.serverConfig.codec === 'opus'" @click="selectCodec('opus')">
              Opus
            </Button>
            <Button variant="toggle" :active="snapcastStore.serverConfig.codec === 'flac'" @click="selectCodec('flac')">
              FLAC
            </Button>
            <Button variant="toggle" :active="snapcastStore.serverConfig.codec === 'pcm'" @click="selectCodec('pcm')">
              PCM
            </Button>
          </div>
        </div>
      </div>
    </section>

            <Button v-if="snapcastStore.hasServerConfigChanges" variant="primary" class="apply-button-sticky"
              :disabled="snapcastStore.isApplyingServerConfig" @click="applyServerConfig">
              {{ snapcastStore.isApplyingServerConfig ? t('multiroom.restarting') : t('multiroomSettings.apply') }}
            </Button>
          </div>
        </Transition>
      </div>
    </div>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useSnapcastStore } from '@/stores/snapcastStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import Button from '@/components/ui/Button.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import Icon from '@/components/ui/Icon.vue';
import InputText from '@/components/ui/InputText.vue';

const { t } = useI18n();
const { on } = useWebSocket();
const snapcastStore = useSnapcastStore();
const unifiedStore = useUnifiedAudioStore();

// Multiroom state
const isMultiroomActive = computed(() => unifiedStore.systemState.multiroom_enabled);

// ResizeObserver pour animer la hauteur
const contentWrapperRef = ref(null);
const containerHeight = ref('auto');
let resizeObserver = null;
let isFirstResize = true;

// Noms des clients (état local pour l'input)
const clientNames = ref({});

// Clients triés avec "milo" en premier
const sortedSnapcastClients = computed(() => snapcastStore.sortedClients);

const audioPresets = computed(() => [
  {
    id: 'reactivity',
    name: t('multiroomSettings.reactivity'),
    config: { buffer: 150, codec: 'opus', chunk_ms: 10 }
  },
  {
    id: 'balanced',
    name: t('multiroomSettings.balanced'),
    config: { buffer: 1000, codec: 'opus', chunk_ms: 20 }
  },
  {
    id: 'quality',
    name: t('multiroomSettings.optimalQuality'),
    config: { buffer: 1500, codec: 'flac', chunk_ms: 40 }
  }
]);

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
  const current = snapcastStore.serverConfig;
  const presetConfig = preset.config;
  return current.buffer === presetConfig.buffer &&
    current.codec === presetConfig.codec &&
    current.chunk_ms === presetConfig.chunk_ms;
}

// === MULTIROOM - CLIENTS ===

async function loadSnapcastData() {
  // Charger les clients et la config serveur depuis le store
  await Promise.all([
    snapcastStore.loadClients(),
    snapcastStore.loadServerConfig()
  ]);

  // Initialiser les noms locaux
  clientNames.value = {};
  snapcastStore.clients.forEach(client => {
    clientNames.value[client.id] = client.name || client.host;
  });
}

async function updateClientName(clientId) {
  const newName = clientNames.value[clientId]?.trim();
  if (!newName) return;

  await snapcastStore.updateClientName(clientId, newName);
}

// === MULTIROOM - SERVER CONFIG ===

function applyPreset(preset) {
  snapcastStore.applyPreset(preset);
}

function selectCodec(codecName) {
  snapcastStore.selectCodec(codecName);
}

async function applyServerConfig() {
  await snapcastStore.applyServerConfig();
}

// === INPUT HANDLERS ===

function handleInputBlur(clientId) {
  // Sauvegarder les changements quand l'utilisateur quitte le champ
  updateClientName(clientId);
}

// === RESIZE OBSERVER ===
function setupResizeObserver() {
  if (resizeObserver) {
    resizeObserver.disconnect();
  }

  resizeObserver = new ResizeObserver(entries => {
    if (entries[0]) {
      const newHeight = entries[0].contentRect.height;

      // Première fois : initialiser sans transition
      if (isFirstResize) {
        containerHeight.value = `${newHeight}px`;
        isFirstResize = false;
        return;
      }

      const currentHeight = parseFloat(containerHeight.value);

      if (Math.abs(newHeight - currentHeight) > 2) {
        containerHeight.value = `${newHeight}px`;
      }
    }
  });

  if (contentWrapperRef.value) {
    resizeObserver.observe(contentWrapperRef.value);
  }
}

// WebSocket listener
const handleClientNameChanged = (msg) => {
  const { client_id, name } = msg.data;
  if (clientNames.value[client_id] !== undefined) {
    clientNames.value[client_id] = name;
  }
  // Le store se met à jour via son propre handler
  snapcastStore.handleClientNameChanged(msg);
};

// Watcher pour charger les données quand multiroom est activé
watch(isMultiroomActive, async (newValue, oldValue) => {
  if (newValue && !oldValue) {
    // Multiroom vient d'être activé, charger les données
    await loadSnapcastData();
  }
});

onMounted(async () => {
  // Ne charger que si multiroom est activé
  if (isMultiroomActive.value) {
    await loadSnapcastData();
  }

  // Setup ResizeObserver après le prochain tick pour que la ref soit disponible
  await nextTick();
  setupResizeObserver();

  on('snapcast', 'client_name_changed', handleClientNameChanged);
});

onUnmounted(() => {
  if (resizeObserver) {
    resizeObserver.disconnect();
  }
});
</script>

<style scoped>
.multiroom-settings {
  display: flex;
  flex-direction: column;
}

.content-container {
  transition: height var(--transition-spring);
  overflow: visible;
  position: relative;
}

.content-wrapper {
  display: flex;
  flex-direction: column;
  overflow: visible;
  border-radius: var(--radius-04);
  transition: background 400ms ease;
  position: relative;
}

.content-wrapper.with-background {
  background: var(--color-background-neutral);
}

.message-content {
  display: flex;
  min-height: 232px;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: var(--space-04);
  padding: var(--space-05);
}

.message-content .text-mono {
  text-align: center;
  color: var(--color-text-secondary);
}

.settings-container {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  padding: var(--space-05-fixed) var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-05-fixed);
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


/* Transitions pour message */
.message-enter-active {
  transition: opacity 300ms ease, transform 300ms ease;
}

.message-leave-active {
  transition: opacity 300ms ease, transform 300ms ease;
  position: absolute;
  width: 100%;
  top: 0;
  left: 0;
}

.message-enter-from {
  opacity: 0;
  transform: translateY(12px);
}

.message-leave-to {
  opacity: 0;
  transform: translateY(-12px);
}

/* Transitions pour settings */
.settings-enter-active {
  transition: opacity 300ms ease 100ms, transform 300ms ease 100ms;
}

.settings-leave-active {
  transition: opacity 300ms ease, transform 300ms ease;
  position: absolute;
  width: 100%;
  top: 0;
  left: 0;
}

.settings-enter-from {
  opacity: 0;
  transform: translateY(12px);
}

.settings-leave-to {
  opacity: 0;
  transform: translateY(-12px);
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

  .message-content {
    min-height: 364px;
  }
}
</style>
