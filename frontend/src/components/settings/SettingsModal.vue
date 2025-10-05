<!-- frontend/src/components/settings/SettingsModal.vue -->
<template>
  <div class="settings-modal">
    <!-- Vue Home : Liste des catégories -->
    <div v-if="currentView === 'home'" class="view-home">
      <ModalHeader :title="t('Configuration de Milō')" />

      <div class="settings-nav-grid">
        <button class="nav-button" @click="goToView('languages')">
          <Icon name="settings" :size="32" />
          <span class="nav-button-text text-body">{{ t('Langues') }}</span>
          <Icon name="caretRight" :size="24" />
        </button>

        <button class="nav-button" @click="goToView('apps')">
          <Icon name="settings" :size="32" />
          <span class="nav-button-text text-body">{{ t('Applications') }}</span>
          <Icon name="caretRight" :size="24" />
        </button>

        <button class="nav-button" @click="goToView('volume')">
          <Icon name="settings" :size="32" />
          <span class="nav-button-text text-body">{{ t('Volume') }}</span>
          <Icon name="caretRight" :size="24" />
        </button>

        <button class="nav-button" @click="goToView('screen')">
          <Icon name="settings" :size="32" />
          <span class="nav-button-text text-body">{{ t('Écran') }}</span>
          <Icon name="caretRight" :size="24" />
        </button>

        <button class="nav-button" @click="goToView('spotify')">
          <AppIcon name="librespot" :size="32" />
          <span class="nav-button-text text-body">Spotify</span>
          <Icon name="caretRight" :size="24" />
        </button>

        <button class="nav-button" @click="goToView('multiroom')">
          <AppIcon name="multiroom" :size="32" />
          <span class="nav-button-text text-body">Multiroom</span>
          <Icon name="caretRight" :size="24" />
        </button>

        <button class="nav-button" @click="goToView('dependencies')">
          <Icon name="settings" :size="32" />
          <span class="nav-button-text text-body">{{ t('Dépendances') }}</span>
          <Icon name="caretRight" :size="24" />
        </button>

        <button class="nav-button" @click="goToView('info')">
          <Icon name="settings" :size="32" />
          <span class="nav-button-text text-body">{{ t('Informations') }}</span>
          <Icon name="caretRight" :size="24" />
        </button>
      </div>
    </div>

    <!-- Vue Langues -->
    <div v-else-if="currentView === 'languages'" class="view-detail">
      <ModalHeader :title="t('Langues')" show-back @back="goToHome" />

      <div class="settings-container">
        <section class="settings-section">
          <div class="language-grid">
            <button v-for="language in availableLanguages" :key="language.code"
              @click="updateSetting('language', { language: language.code })"
              :class="['language-button', { active: currentLanguage === language.code }]">
              <span class="language-flag">{{ language.flag }}</span>
              <span class="language-name heading-2">{{ language.name }}</span>
            </button>
          </div>
        </section>
      </div>
    </div>

    <!-- Vue Applications -->
    <div v-else-if="currentView === 'apps'" class="view-detail">
      <ModalHeader :title="t('Applications')" show-back @back="goToHome" />

      <div class="settings-container">
        <section class="settings-section">
          <!-- Sources audio -->
          <div class="setting-item-container">
            <p class="app-group-title text-mono">{{ t('Sources audio') }}</p>

            <div class="app-list">
              <div class="app-item">
                <div class="app-info">
                  <AppIcon name="librespot" :size="32" />
                  <span class="app-name text-body">Spotify</span>
                </div>
                <Toggle v-model="config.dock.apps.librespot" variant="primary" size="compact"
                  :disabled="!canDisableAudioSource('librespot')" @change="updateDockApps" />
              </div>

              <div class="app-item">
                <div class="app-info">
                  <AppIcon name="bluetooth" :size="32" />
                  <span class="app-name text-body">Bluetooth</span>
                </div>
                <Toggle v-model="config.dock.apps.bluetooth" variant="primary" size="compact"
                  :disabled="!canDisableAudioSource('bluetooth')" @change="updateDockApps" />
              </div>

              <div class="app-item">
                <div class="app-info">
                  <AppIcon name="roc" :size="32" />
                  <span class="app-name text-body">{{ t('macOS') }}</span>
                </div>
                <Toggle v-model="config.dock.apps.roc" variant="primary" size="compact"
                  :disabled="!canDisableAudioSource('roc')" @change="updateDockApps" />
              </div>
            </div>
          </div>

          <!-- Fonctionnalités -->
          <div class="setting-item-container">
            <p class="app-group-title text-mono">{{ t('Fonctionnalités') }}</p>

            <div class="app-list">
              <div class="app-item">
                <div class="app-info">
                  <AppIcon name="multiroom" :size="32" />
                  <span class="app-name text-body">Multiroom</span>
                </div>
                <Toggle v-model="config.dock.apps.multiroom" variant="primary" size="compact"
                  @change="updateDockApps" />
              </div>

              <div class="app-item">
                <div class="app-info">
                  <AppIcon name="equalizer" :size="32" />
                  <span class="app-name text-body">{{ t('Égaliseur') }}</span>
                </div>
                <Toggle v-model="config.dock.apps.equalizer" variant="primary" size="compact"
                  @change="updateDockApps" />
              </div>

              <div class="app-item">
                <div class="app-info">
                  <AppIcon name="settings" :size="32" />
                  <span class="app-name text-body">{{ t('Paramètres') }}</span>
                </div>
                <Toggle v-model="config.dock.apps.settings" variant="primary" size="compact" @change="updateDockApps" />
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>

    <!-- Vue Volume -->
    <div v-else-if="currentView === 'volume'" class="view-detail">
      <ModalHeader :title="t('Volume')" show-back @back="goToHome" />

      <div class="settings-container">
        <section class="settings-section">
          <!-- Contrôles du volume -->
          <div class="volume-group">
            <h2 class="heading-2 text-body">{{ t('Contrôles du volume') }}</h2>

            <div class="setting-item-container">
              <div class="volume-item-setting text-mono">
                {{ t('Incrémentation du rotary encoder') }}
              </div>
              <div class="volume-steps-control">
                <RangeSlider v-model="config.volume.rotary_volume_steps" :min="1" :max="10" :step="1" value-unit="%"
                  @input="debouncedUpdate('rotary-steps', { rotary_volume_steps: $event })" />
              </div>
            </div>

            <div class="setting-item-container">
              <div class="volume-item-setting text-mono">
                {{ t('Incrémentation des boutons volume en mobile') }}
              </div>
              <div class="volume-steps-control">
                <RangeSlider v-model="config.volume.mobile_volume_steps" :min="1" :max="10" :step="1" value-unit="%"
                  @input="debouncedUpdate('volume-steps', { mobile_volume_steps: $event })" />
              </div>
            </div>
          </div>

          <div class="settings-separator"></div>

          <!-- Limites du volume -->
          <div class="volume-group">
            <h2 class="heading-2 text-body">{{ t('Limites du volume') }}</h2>
            <div class="setting-item-container">
              <div class="volume-item-setting text-mono">
                {{ t('Volume minimal et maximal') }}
              </div>
              <div class="volume-limits-control">
                <DoubleRangeSlider v-model="config.volume.limits" :min="0" :max="100" :step="1" :gap="10" value-unit="%"
                  @input="updateVolumeLimits" />
              </div>
            </div>
          </div>

          <div class="settings-separator"></div>

          <!-- Volume au démarrage -->
          <div class="volume-group">
            <h2 class="heading-2 text-body">{{ t('Volume au démarrage') }}</h2>

            <div class="startup-mode-buttons">
              <Button variant="toggle" :active="!config.volume.restore_last_volume"
                @click="updateSetting('volume-startup', { startup_volume: config.volume.startup_volume, restore_last_volume: false })">
                {{ t('Volume fixe') }}
              </Button>
              <Button variant="toggle" :active="config.volume.restore_last_volume"
                @click="updateSetting('volume-startup', { startup_volume: config.volume.startup_volume, restore_last_volume: true })">
                {{ t('Restaurer le dernier') }}
              </Button>
            </div>

            <div v-if="!config.volume.restore_last_volume" class="setting-item-container">
              <div class="volume-item-setting text-mono">
                {{ t('Volume fixe au démarrage') }}
              </div>
              <div class="startup-volume-control">
                <RangeSlider v-model="config.volume.startup_volume" :min="0" :max="100" :step="1" value-unit="%"
                  @input="debouncedUpdate('volume-startup', { startup_volume: $event, restore_last_volume: false })" />
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>

    <!-- Vue Écran -->
    <div v-else-if="currentView === 'screen'" class="view-detail">
      <ModalHeader :title="t('Écran')" show-back @back="goToHome" />

      <div class="settings-container">
        <section class="settings-section">
          <!-- Luminosité -->
          <div class="screen-group">
            <h2 class="heading-2 text-body">{{ t('Luminosité') }}</h2>
            <div class="setting-item-container">
              <div class="screen-description text-mono">
                {{ t('Intensité de la luminosité') }}
              </div>
              <div class="brightness-control">
                <RangeSlider v-model="config.screen.brightness_on" :min="1" :max="10" :step="1" value-unit=""
                  @input="handleBrightnessChange" />
              </div>
            </div>
          </div>

          <div class="settings-separator"></div>

          <!-- Mise en veille automatique -->
          <div class="screen-group">
            <h2 class="heading-2 text-body">{{ t('Mise en veille automatique') }}</h2>
            <div class="setting-item-container">
              <div class="screen-description text-mono">
                {{ t('Délai de la mise en veille après :') }}
              </div>
              <div class="timeout-buttons">
                <Button v-for="timeout in timeoutPresets" :key="timeout.value" variant="toggle"
                  :active="isTimeoutActive(timeout.value)" @click="setScreenTimeout(timeout.value)">
                  {{ timeout.label }}
                </Button>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>

    <!-- Vue Spotify -->
    <div v-else-if="currentView === 'spotify'" class="view-detail">
      <ModalHeader title="Spotify" show-back @back="goToHome" />

      <div class="settings-container">
        <section class="settings-section">
          <div class="spotify-group">
            <h2 class="heading-2 text-body">{{ t('Déconnexion automatique') }}</h2>
            <div class="setting-item-container">
              <div class="spotify-description text-mono">
                {{ t('Délai de déconnexion après que la musique soit en pause pendant :') }}
              </div>
              <div class="disconnect-buttons">
                <Button v-for="delay in disconnectPresets" :key="delay.value" variant="toggle"
                  :active="isDisconnectActive(delay.value)" @click="setSpotifyDisconnect(delay.value)">
                  {{ delay.label }}
                </Button>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>

    <!-- Vue Multiroom -->
    <div v-else-if="currentView === 'multiroom'" class="view-detail">
      <ModalHeader title="Multiroom" show-back @back="goToHome" />

      <div class="settings-container">
        <section class="settings-section">
          <!-- Enceintes multiroom -->
          <div class="multiroom-group">
            <h2 class="heading-2 text-body">{{ t('Enceintes multiroom') }}</h2>

            <div v-if="loadingClients" class="loading-state">
              <p class="text-mono">{{ t('Chargement des enceintes...') }}</p>
            </div>

            <div v-else-if="snapcastClients.length === 0" class="no-clients-state">
              <p class="text-mono">{{ t('Aucune enceinte connectée') }}</p>
            </div>

            <div v-else class="clients-list">
              <div v-for="client in snapcastClients" :key="client.id" class="client-config-item">
                <div class="client-info-wrapper">
                  <span class="client-hostname text-mono">{{ client.host }}</span>
                  <input type="text" v-model="clientNames[client.id]" :placeholder="client.host"
                    class="client-name-input text-body" maxlength="50" @blur="updateClientName(client.id)"
                    @keyup.enter="updateClientName(client.id)">
                </div>
              </div>
            </div>
          </div>

          <div class="settings-separator"></div>

          <!-- Presets audio -->
          <div class="multiroom-group">
            <h2 class="heading-2 text-body">{{ t('Presets audio') }}</h2>
            <div class="presets-buttons">
              <Button v-for="preset in audioPresets" :key="preset.id" variant="toggle" :active="isPresetActive(preset)"
                :disabled="applyingServerConfig" @click="applyPreset(preset)">
                {{ preset.name }}
              </Button>
            </div>
          </div>

          <div class="settings-separator"></div>

          <!-- Paramètres avancés -->
          <div class="multiroom-group">
            <h2 class="heading-2 text-body">{{ t('Paramètres avancés') }}</h2>

            <div class="form-group">
              <label class="text-mono">{{ t('Buffer global (ms)') }}</label>
              <RangeSlider v-model="serverConfig.buffer" :min="100" :max="2000" :step="50" value-unit="ms" />
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

            <div class="form-group">
              <label class="text-mono">{{ t('Taille des chunks (ms)') }}</label>
              <RangeSlider v-model="serverConfig.chunk_ms" :min="10" :max="100" :step="5" value-unit="ms" />
            </div>

            <Button variant="primary" :disabled="loadingServerConfig || applyingServerConfig || !hasServerConfigChanges"
              @click="applyServerConfig">
              {{ applyingServerConfig ? t('Redémarrage du multiroom en cours...') : t('Appliquer') }}
            </Button>
          </div>
        </section>
      </div>
    </div>

    <!-- Vue Dépendances -->
    <div v-else-if="currentView === 'dependencies'" class="view-detail">
      <ModalHeader :title="t('Dépendances')" show-back @back="goToHome" />

      <div class="settings-container">
        <DependenciesManager />
      </div>
    </div>

    <!-- Vue Informations -->
    <div v-else-if="currentView === 'info'" class="view-detail">
      <ModalHeader :title="t('Informations')" show-back @back="goToHome" />

      <div class="settings-container">
        <section class="settings-section">
          <div class="info-grid">
            <div class="info-item">
              <span class="info-label text-mono">{{ t('Version de Milo') }}</span>
              <span class="info-value text-mono">0.1.0</span>
            </div>

            <div class="info-item">
              <span class="info-label text-mono">{{ t('Température') }}</span>
              <span class="info-value text-mono">
                <span v-if="temperatureLoading && systemTemperature === null">...</span>
                <span v-else-if="systemTemperature !== null">{{ systemTemperature.toFixed(1) }}°C</span>
                <span v-else class="text-error">{{ t('Non disponible') }}</span>
              </span>
            </div>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { i18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import ModalHeader from '@/components/ui/ModalHeader.vue';
import Icon from '@/components/ui/Icon.vue';
import IconButton from '@/components/ui/IconButton.vue';
import Toggle from '@/components/ui/Toggle.vue';
import Button from '@/components/ui/Button.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import DoubleRangeSlider from '@/components/ui/DoubleRangeSlider.vue';
import AppIcon from '@/components/ui/AppIcon.vue';
import DependenciesManager from '@/components/settings/DependenciesManager.vue';
import axios from 'axios';


const emit = defineEmits(['close']);

const { t, setLanguage, getAvailableLanguages, getCurrentLanguage } = useI18n();
const { on } = useWebSocket();

// Navigation
const currentView = ref('home');

function goToView(view) {
  currentView.value = view;
}

function goToHome() {
  currentView.value = 'home';
}

// Config
const config = ref({
  volume: {
    mobile_volume_steps: 5,
    rotary_volume_steps: 2,
    limits: { min: 0, max: 65 },
    restore_last_volume: false,
    startup_volume: 37
  },
  screen: {
    brightness_on: 5,
    timeout_enabled: true,
    timeout_seconds: 10
  },
  spotify: {
    auto_disconnect_delay: 10.0
  },
  dock: {
    apps: {
      librespot: true,
      bluetooth: true,
      roc: true,
      multiroom: true,
      equalizer: true,
      settings: true
    }
  }
});

// Multiroom - Clients
const snapcastClients = ref([]);
const loadingClients = ref(false);
const clientNames = ref({});

// Multiroom - Server config
const serverConfig = ref({
  buffer: 1000,
  codec: 'flac',
  chunk_ms: 20,
  sampleformat: '48000:16:2'
});
const originalServerConfig = ref({});
const loadingServerConfig = ref(false);
const applyingServerConfig = ref(false);

const systemTemperature = ref(null);
const temperatureLoading = ref(false);

const availableLanguages = computed(() => getAvailableLanguages());
const currentLanguage = computed(() => getCurrentLanguage());

const timeoutPresets = computed(() => [
  { value: 10, label: t('10 secondes') },
  { value: 180, label: t('3 minutes') },
  { value: 900, label: t('15 minutes') },
  { value: 1800, label: t('30 minutes') },
  { value: 3600, label: t('1 heure') },
  { value: 0, label: t('Jamais') }
]);

const disconnectPresets = computed(() => [
  { value: 10, label: t('10 secondes') },
  { value: 180, label: t('3 minutes') },
  { value: 900, label: t('15 minutes') },
  { value: 1800, label: t('30 minutes') },
  { value: 3600, label: t('1 heure') },
  { value: 0, label: t('Jamais') }
]);

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

function canDisableAudioSource(sourceId) {
  const audioSources = ['librespot', 'bluetooth', 'roc'];
  const enabledAudioSources = audioSources.filter(source =>
    config.value.dock.apps[source] && source !== sourceId
  );
  return enabledAudioSources.length > 0;
}

function getEnabledAppsArray() {
  return Object.keys(config.value.dock.apps).filter(app => config.value.dock.apps[app]);
}

function isTimeoutActive(value) {
  if (value === 0) {
    return config.value.screen.timeout_seconds === 0;
  }
  return config.value.screen.timeout_seconds === value;
}

function isDisconnectActive(value) {
  if (value === 0) {
    return config.value.spotify.auto_disconnect_delay === 0;
  }
  return config.value.spotify.auto_disconnect_delay === value;
}

function isPresetActive(preset) {
  const current = serverConfig.value;
  const presetConfig = preset.config;
  return current.buffer === presetConfig.buffer &&
    current.codec === presetConfig.codec &&
    current.chunk_ms === presetConfig.chunk_ms;
}

let debounceTimeout = null;
function debouncedUpdate(endpoint, payload, delay = 800) {
  clearTimeout(debounceTimeout);
  debounceTimeout = setTimeout(() => updateSetting(endpoint, payload), delay);
}

async function updateSetting(endpoint, payload) {
  try {
    await axios.post(`/api/settings/${endpoint}`, payload);
  } catch (error) {
    console.error(`Error updating ${endpoint}:`, error);
  }
}

function updateDockApps() {
  const enabledApps = getEnabledAppsArray();
  debouncedUpdate('dock-apps', { enabled_apps: enabledApps }, 500);
}

function updateVolumeLimits(limits) {
  debouncedUpdate('volume-limits', {
    alsa_min: limits.min,
    alsa_max: limits.max
  });
}

let brightnessInstantTimeout = null;
let brightnessDebounceTimeout = null;

function handleBrightnessChange(value) {
  clearTimeout(brightnessInstantTimeout);
  brightnessInstantTimeout = setTimeout(() => {
    axios.post('/api/settings/screen-brightness/apply', { brightness_on: value }).catch(console.error);
  }, 300);

  clearTimeout(brightnessDebounceTimeout);
  brightnessDebounceTimeout = setTimeout(() => {
    updateSetting('screen-brightness', { brightness_on: value });
  }, 1000);
}

function setScreenTimeout(value) {
  updateSetting('screen-timeout', {
    screen_timeout_enabled: value !== 0,
    screen_timeout_seconds: value
  });
}

function setSpotifyDisconnect(value) {
  updateSetting('spotify-disconnect', { auto_disconnect_delay: value });
}

// === MULTIROOM - CLIENTS ===

async function loadSnapcastClients() {
  loadingClients.value = true;
  try {
    const response = await axios.get('/api/routing/snapcast/clients');
    if (response.data.clients) {
      snapcastClients.value = response.data.clients;

      clientNames.value = {};
      response.data.clients.forEach(client => {
        clientNames.value[client.id] = client.name || client.host;
      });
    }
  } catch (error) {
    console.error('Error loading snapcast clients:', error);
  } finally {
    loadingClients.value = false;
  }
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
  loadingServerConfig.value = true;
  try {
    const response = await axios.get('/api/routing/snapcast/server-config');
    const fileConfig = response.data.config?.file_config?.parsed_config?.stream || {};
    const streamConfig = response.data.config?.stream_config || {};

    serverConfig.value = {
      buffer: parseInt(fileConfig.buffer || streamConfig.buffer_ms || '1000'),
      codec: fileConfig.codec || streamConfig.codec || 'flac',
      chunk_ms: parseInt(fileConfig.chunk_ms || streamConfig.chunk_ms) || 20,
      sampleformat: '48000:16:2'
    };

    originalServerConfig.value = JSON.parse(JSON.stringify(serverConfig.value));
  } catch (error) {
    console.error('Error loading server config:', error);
  } finally {
    loadingServerConfig.value = false;
  }
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

// === TEMPÉRATURE ===

async function loadSystemTemperature() {
  if (temperatureLoading.value) return;

  try {
    temperatureLoading.value = true;
    const response = await axios.get('/api/settings/system-temperature');

    if (response.data.status === 'success' && response.data.temperature !== null) {
      systemTemperature.value = response.data.temperature;
    } else {
      systemTemperature.value = null;
    }
  } catch (error) {
    console.error('Error loading temperature:', error);
    systemTemperature.value = null;
  } finally {
    temperatureLoading.value = false;
  }
}

let temperatureInterval = null;

async function loadAllConfigs() {
  try {
    const [volumeLimits, volumeStartup, volumeSteps, rotarySteps, spotify, screenTimeout, brightness, dockApps] = await Promise.all([
      axios.get('/api/settings/volume-limits'),
      axios.get('/api/settings/volume-startup'),
      axios.get('/api/settings/volume-steps'),
      axios.get('/api/settings/rotary-steps'),
      axios.get('/api/settings/spotify-disconnect'),
      axios.get('/api/settings/screen-timeout'),
      axios.get('/api/settings/screen-brightness'),
      axios.get('/api/settings/dock-apps')
    ]);

    if (volumeLimits.data.status === 'success') {
      config.value.volume.limits = {
        min: volumeLimits.data.limits.alsa_min || 0,
        max: volumeLimits.data.limits.alsa_max || 65
      };
    }

    if (volumeStartup.data.status === 'success') {
      config.value.volume.restore_last_volume = volumeStartup.data.config.restore_last_volume || false;
      config.value.volume.startup_volume = volumeStartup.data.config.startup_volume || 37;
    }

    if (volumeSteps.data.status === 'success') {
      config.value.volume.mobile_volume_steps = volumeSteps.data.config.mobile_volume_steps || 5;
    }

    if (rotarySteps.data.status === 'success') {
      config.value.volume.rotary_volume_steps = rotarySteps.data.config.rotary_volume_steps || 2;
    }

    if (spotify.data.status === 'success') {
      config.value.spotify.auto_disconnect_delay = spotify.data.config.auto_disconnect_delay ?? 10.0;
    }

    if (screenTimeout.data.status === 'success') {
      config.value.screen.timeout_seconds = screenTimeout.data.config.screen_timeout_seconds ?? 10;
      config.value.screen.timeout_enabled = config.value.screen.timeout_seconds !== 0;
    }

    if (brightness.data.status === 'success') {
      config.value.screen.brightness_on = brightness.data.config.brightness_on || 5;
    }

    if (dockApps.data.status === 'success') {
      const enabledApps = dockApps.data.config.enabled_apps || ['librespot', 'bluetooth', 'roc', 'multiroom', 'equalizer', 'settings'];

      const appsObj = {};
      ['librespot', 'bluetooth', 'roc', 'multiroom', 'equalizer', 'settings'].forEach(app => {
        appsObj[app] = enabledApps.includes(app);
      });

      config.value.dock.apps = appsObj;
    }
  } catch (error) {
    console.error('Error loading configs:', error);
  }
}

const wsListeners = {
  language_changed: (msg) => i18n.handleLanguageChanged(msg.data?.language),
  volume_limits_changed: (msg) => {
    if (msg.data?.limits) {
      config.value.volume.limits = {
        min: msg.data.limits.alsa_min || 0,
        max: msg.data.limits.alsa_max || 65
      };
    }
  },
  volume_startup_changed: (msg) => {
    if (msg.data?.config) {
      config.value.volume.restore_last_volume = msg.data.config.restore_last_volume;
      config.value.volume.startup_volume = msg.data.config.startup_volume;
    }
  },
  volume_steps_changed: (msg) => {
    if (msg.data?.config?.mobile_volume_steps) {
      config.value.volume.mobile_volume_steps = msg.data.config.mobile_volume_steps;
    }
  },
  rotary_steps_changed: (msg) => {
    if (msg.data?.config?.rotary_volume_steps) {
      config.value.volume.rotary_volume_steps = msg.data.config.rotary_volume_steps;
    }
  },
  spotify_disconnect_changed: (msg) => {
    if (msg.data?.config?.auto_disconnect_delay !== undefined) {
      config.value.spotify.auto_disconnect_delay = msg.data.config.auto_disconnect_delay;
    }
  },
  screen_timeout_changed: (msg) => {
    if (msg.data?.config) {
      config.value.screen.timeout_seconds = msg.data.config.screen_timeout_seconds;
      config.value.screen.timeout_enabled = config.value.screen.timeout_seconds !== 0;
    }
  },
  screen_brightness_changed: (msg) => {
    if (msg.data?.config?.brightness_on !== undefined) {
      config.value.screen.brightness_on = msg.data.config.brightness_on;
    }
  },
  dock_apps_changed: (msg) => {
    if (msg.data?.config?.enabled_apps) {
      const enabledApps = msg.data.config.enabled_apps;
      const appsObj = {};
      ['librespot', 'bluetooth', 'roc', 'multiroom', 'equalizer', 'settings'].forEach(app => {
        appsObj[app] = enabledApps.includes(app);
      });
      config.value.dock.apps = appsObj;
    }
  },
  client_name_changed: (msg) => {
    const { client_id, name } = msg.data;
    if (clientNames.value[client_id] !== undefined) {
      clientNames.value[client_id] = name;
    }
    const client = snapcastClients.value.find(c => c.id === client_id);
    if (client) {
      client.name = name;
    }
  }
};

onMounted(async () => {
  await i18n.initializeLanguage();
  await loadAllConfigs();
  await loadSystemTemperature();
  await loadSnapcastClients();
  await loadServerConfig();

  temperatureInterval = setInterval(loadSystemTemperature, 5000);

  Object.entries(wsListeners).forEach(([eventType, handler]) => {
    if (eventType === 'client_name_changed') {
      on('snapcast', eventType, handler);
    } else {
      on('settings', eventType, handler);
    }
  });
});

onUnmounted(() => {
  if (temperatureInterval) {
    clearInterval(temperatureInterval);
  }
});
</script>

<style scoped>
.settings-modal {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.view-home,
.view-detail {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

/* Navigation Grid */
.settings-nav-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-02);
}

.nav-button {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-03);
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.nav-button:hover {
  background: var(--color-background-strong);
  border-color: var(--color-background-glass);
}

.nav-button-text {
  flex: 1;
  text-align: left;
  color: var(--color-text);
}

/* Settings Container */
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

.settings-separator {
  height: 1px;
  background: var(--color-background-strong);
  margin: var(--space-02) 0;
}

/* Languages */
.language-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-02);
}

.language-button {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-02) var(--space-02) var(--space-02) var(--space-03);
  background: var(--color-background-strong);
  border: 2px solid transparent;
  border-radius: var(--radius-04);
  cursor: pointer;
  transition: all var(--transition-fast);
  width: 100%;
  text-align: left;
}

.language-button:hover {
  background: var(--color-background);
  border-color: var(--color-background-glass);
}

.language-button.active {
  background: var(--color-background);
  border-color: var(--color-brand);
}

.language-flag {
  font-size: 20px;
  width: 24px;
  text-align: center;
}

.language-name {
  flex: 1;
  color: var(--color-text);
  font-size: var(--font-size-body);
}

/* Applications */
.app-group-title {
  color: var(--color-text-secondary);
  font-weight: 500;
}

.app-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-02);
}

.app-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-02);
  background: var(--color-background-strong);
  border-radius: var(--radius-04);
}

.app-info {
  display: flex;
  align-items: center;
  gap: var(--space-02);
}

.app-name {
  color: var(--color-text);
}

/* Volume, Screen, Spotify, Multiroom */
.volume-group,
.screen-group,
.spotify-group,
.multiroom-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.setting-item-container {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.volume-item-setting,
.screen-description,
.spotify-description {
  color: var(--color-text-secondary);
}

.volume-steps-control,
.brightness-control,
.startup-volume-control {
  display: flex;
  align-items: center;
}

.volume-limits-control {
  display: flex;
  flex-direction: column;
}

.startup-mode-buttons {
  display: flex;
  gap: var(--space-02);
}

.startup-mode-buttons .btn {
  flex: 1;
}

.timeout-buttons,
.disconnect-buttons {
  display: flex;
  gap: var(--space-02);
  flex-wrap: wrap;
}

.timeout-buttons .btn,
.disconnect-buttons .btn {
  flex: 1;
  min-width: 150px;
}

/* Multiroom clients */
.loading-state,
.no-clients-state {
  text-align: center;
  padding: var(--space-04);
  color: var(--color-text-secondary);
}

.clients-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.client-config-item {
  background: var(--color-background-strong);
  border-radius: var(--radius-04);
  padding: var(--space-03) var(--space-04);
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
  background: var(--color-background);
  color: var(--color-text);
  transition: border-color var(--transition-fast);
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

/* Info */
.info-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-02);
}

.info-item {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: var(--space-03) var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-background-strong);
}

.info-label {
  color: var(--color-text-secondary);
}

.info-value {
  color: var(--color-text);
  text-align: right;
}

.text-error {
  color: var(--color-destructive);
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .settings-nav-grid {
    grid-template-columns: 1fr;
  }

  .language-grid {
    grid-template-columns: 1fr 1fr;
  }

  .info-grid {
    grid-template-columns: 1fr;
  }

  .app-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-02);
  }

  .timeout-buttons,
  .disconnect-buttons {
    display: flex;
    gap: var(--space-02);
    flex-wrap: wrap;
  }

  .startup-mode-buttons {
    flex-direction: column;
  }

  .volume-steps-control,
  .brightness-control,
  .startup-volume-control {
    gap: var(--space-05);
  }

  .app-item {
    padding: var(--space-02);
  }

  .codec-buttons,
  .presets-buttons {
    flex-direction: column;
  }
}
</style>