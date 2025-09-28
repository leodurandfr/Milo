<!-- frontend/src/views/SettingsView.vue - Avec section Dépendances -->
<template>
  <div class="settings-view">
    <div class="settings-modal">
      <!-- Contenu scrollable avec header à l'intérieur -->
      <div class="modal-content" ref="modalContent" @pointerdown="handlePointerDown" @pointermove="handlePointerMove"
        @pointerup="handlePointerUp" @pointercancel="handlePointerUp">

        <!-- Header noir avec bouton retour -->
        <div class="modal-header">
          <div class="back-button-wrapper">
            <IconButton icon="caretLeft" variant="dark" @click="goBack" />
            <h2 class="heading-2">{{ t('Configuration de Milō') }}</h2>
          </div>
        </div>

        <!-- 1. Languages -->
        <section class="settings-section">
          <h1 class="heading-1">{{ t('Langues') }}</h1>

          <div class="language-grid">
            <button v-for="language in availableLanguages" :key="language.code"
              @click="updateSetting('language', { language: language.code })"
              :class="['language-button', { active: currentLanguage === language.code }]">
              <span class="language-flag">{{ language.flag }}</span>
              <span class="language-name heading-2">{{ language.name }}</span>
            </button>
          </div>
        </section>

        <!-- 2. Applications -->
        <section class="settings-section">
          <h1 class="heading-1">{{ t('Applications') }}</h1>

          <!-- Sources audio -->
          <div class="app-group">
            <p class="app-group-title text-mono">{{ t('Sources audio') }}</p>

            <div class="app-list">
              <div class="app-item">
                <div class="app-info">
                  <AppIcon name="spotify" :size="32" />
                  <span class="app-name text-body">Spotify</span>
                </div>
                <Toggle v-model="config.dock.apps.librespot" variant="primary"
                  :disabled="!canDisableAudioSource('librespot')" @change="updateDockApps" />
              </div>

              <div class="app-item">
                <div class="app-info">
                  <AppIcon name="bluetooth" :size="32" />
                  <span class="app-name text-body">Bluetooth</span>
                </div>
                <Toggle v-model="config.dock.apps.bluetooth" variant="primary"
                  :disabled="!canDisableAudioSource('bluetooth')" @change="updateDockApps" />
              </div>

              <div class="app-item">
                <div class="app-info">
                  <AppIcon name="roc" :size="32" />
                  <span class="app-name text-body">{{ t('Réception audio Mac') }}</span>
                </div>
                <Toggle v-model="config.dock.apps.roc" variant="primary" :disabled="!canDisableAudioSource('roc')"
                  @change="updateDockApps" />
              </div>
            </div>
          </div>

          <!-- Fonctionnalités -->
          <div class="app-group">
            <p class="app-group-title text-mono">{{ t('Fonctionnalités') }}</p>

            <div class="app-list">
              <div class="app-item">
                <div class="app-info">
                  <AppIcon name="multiroom" :size="32" />
                  <span class="app-name text-body">Multiroom</span>
                </div>
                <Toggle v-model="config.dock.apps.multiroom" variant="primary" @change="updateDockApps" />
              </div>

              <div class="app-item">
                <div class="app-info">
                  <AppIcon name="equalizer" :size="32" />
                  <span class="app-name text-body">{{ t('Égaliseur') }}</span>
                </div>
                <Toggle v-model="config.dock.apps.equalizer" variant="primary" @change="updateDockApps" />
              </div>
            </div>
          </div>
        </section>

        <!-- 3. Volume -->
        <section class="settings-section">
          <h1 class="heading-1">{{ t('Volume') }}</h1>

          <!-- Contrôles du volume -->
          <div class="volume-group">
            <h2 class="heading-2 text-body">{{ t('Contrôles du volume') }}</h2>
            <div class="volume-description text-mono">
              {{ t('Incrémentation des boutons volume en mobile') }}
            </div>

            <div class="volume-steps-control">
              <RangeSlider v-model="config.volume.mobile_volume_steps" :min="1" :max="10" :step="1" value-unit="%"
                @input="debouncedUpdate('volume-steps', { mobile_volume_steps: $event })" />
            </div>
          </div>

          <div class="settings-separator"></div>

          <!-- Limites du volume -->
          <div class="volume-group">
            <h2 class="heading-2 text-body">{{ t('Limites du volume') }}</h2>
            <div class="volume-description text-mono">
              {{ t('Volume minimal et maximal') }}
            </div>

            <div class="volume-limits-control">
              <DoubleRangeSlider v-model="config.volume.limits" :min="0" :max="100" :step="1" :gap="10" value-unit="%"
                @input="updateVolumeLimits" />
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

            <!-- Volume fixe slider (sans container background) -->
            <div v-if="!config.volume.restore_last_volume" class="fixed-volume-control">
              <div class="volume-description text-mono">
                {{ t('Volume fixe au démarrage') }}
              </div>
              <div class="startup-volume-control">
                <RangeSlider v-model="config.volume.startup_volume" :min="0" :max="100" :step="1" value-unit="%"
                  @input="debouncedUpdate('volume-startup', { startup_volume: $event, restore_last_volume: false })" />
              </div>
            </div>
          </div>
        </section>

        <!-- 4. Écran -->
        <section class="settings-section">
          <h1 class="heading-1">{{ t('Écran') }}</h1>

          <!-- Luminosité -->
          <div class="screen-group">
            <h2 class="heading-2 text-body">{{ t('Luminosité') }}</h2>

            <div class="screen-description text-mono">
              {{ t('Intensité de la luminosité') }}
            </div>

            <div class="brightness-control">
              <RangeSlider v-model="config.screen.brightness_on" :min="1" :max="10" :step="1" value-unit=""
                @input="handleBrightnessChange" />
            </div>
          </div>

          <div class="settings-separator"></div>

          <!-- Mise en veille automatique -->
          <div class="screen-group">
            <h2 class="heading-2 text-body">{{ t('Mise en veille automatique') }}</h2>

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
        </section>

        <!-- 5. Spotify -->
        <section class="settings-section">
          <h1 class="heading-1">{{ t('Spotify') }}</h1>

          <div class="spotify-group">
            <h2 class="heading-2 text-body">{{ t('Déconnexion automatique') }}</h2>

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
        </section>

        <!-- 6. NOUVEAU : Dépendances -->
        <section class="settings-section">
          <h1 class="heading-1">{{ t('Dépendances') }}</h1>

          <div v-if="dependenciesLoading" class="dependencies-loading">
            <div class="loading-message text-mono">
              {{ t('Vérification des versions...') }}
            </div>
          </div>

          <div v-else-if="dependenciesError" class="dependencies-error">
            <div class="error-message text-mono">
              {{ t('Erreur lors du chargement des dépendances') }}
            </div>
            <Button variant="secondary" @click="loadDependencies">
              {{ t('Réessayer') }}
            </Button>
          </div>

          <div v-else class="dependencies-list">
            <div v-for="(dep, key) in dependencies" :key="key" class="dependency-item">
              <div class="dependency-header">
                <div class="dependency-info">
                  <h3 class="dependency-name text-body">{{ dep.name }}</h3>
                  <p class="dependency-description text-mono">{{ dep.description }}</p>
                </div>
                
                <div class="dependency-status">
                  <div v-if="dep.update_available" class="update-badge text-mono">
                    {{ t('Mise à jour disponible') }}
                  </div>
                  <div v-else-if="getInstallStatus(dep) === 'installed'" class="status-badge status-ok text-mono">
                    {{ t('À jour') }}
                  </div>
                  <div v-else-if="getInstallStatus(dep) === 'not_installed'" class="status-badge status-error text-mono">
                    {{ t('Non installé') }}
                  </div>
                  <div v-else class="status-badge status-unknown text-mono">
                    {{ t('État inconnu') }}
                  </div>
                </div>
              </div>

              <div class="dependency-versions">
                <!-- Version installée -->
                <div class="version-info">
                  <span class="version-label text-mono">{{ t('Installé') }}</span>
                  <span class="version-value text-mono">
                    <span v-if="getInstalledVersion(dep)">{{ getInstalledVersion(dep) }}</span>
                    <span v-else class="text-error">{{ t('Non disponible') }}</span>
                  </span>
                </div>

                <!-- Version disponible - seulement si mise à jour disponible -->
                <div v-if="dep.update_available" class="version-info">
                  <span class="version-label text-mono">{{ t('Disponible') }}</span>
                  <span class="version-value text-mono">
                    <span v-if="getLatestVersion(dep)">{{ getLatestVersion(dep) }}</span>
                    <span v-else-if="getGitHubStatus(dep) === 'error'" class="text-error">{{ t('Erreur API') }}</span>
                    <span v-else>{{ t('Chargement...') }}</span>
                  </span>
                </div>
              </div>

              <!-- Détails d'erreur si nécessaire -->
              <div v-if="dep.installed?.errors?.length" class="dependency-errors">
                <details class="error-details">
                  <summary class="text-mono">{{ t('Détails des erreurs') }}</summary>
                  <ul class="error-list">
                    <li v-for="error in dep.installed.errors" :key="error" class="text-mono">{{ error }}</li>
                  </ul>
                </details>
              </div>
            </div>
          </div>
        </section>

        <!-- 7. Informations -->
        <section class="settings-section">
          <h2 class="heading-2">{{ t('Informations') }}</h2>

          <div class="info-item">
            <span class="info-label text-mono">{{ t('Version de Milo') }}</span>
            <span class="info-value text-mono">0.1.0</span>
          </div>

          <div class="info-item">
            <span class="info-label text-mono">{{ t('Température du système') }}</span>
            <span class="info-value text-mono">
              <span v-if="temperatureLoading && systemTemperature === null">...</span>
              <span v-else-if="systemTemperature !== null">{{ systemTemperature.toFixed(1) }}°C</span>
              <span v-else class="text-error">{{ t('Non disponible') }}</span>
            </span>
          </div>
        </section>

      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useRouter } from 'vue-router';
import { useI18n } from '@/services/i18n';
import { i18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import IconButton from '@/components/ui/IconButton.vue';
import Toggle from '@/components/ui/Toggle.vue';
import Button from '@/components/ui/Button.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import DoubleRangeSlider from '@/components/ui/DoubleRangeSlider.vue';
import AppIcon from '@/components/ui/AppIcon.vue';
import axios from 'axios';

const router = useRouter();
const { t, setLanguage, getAvailableLanguages, getCurrentLanguage } = useI18n();
const { on } = useWebSocket();

// Configuration unifiée 
const config = ref({
  volume: {
    mobile_volume_steps: 5,
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
      equalizer: true
    }
  }
});

// États existants
const systemTemperature = ref(null);
const temperatureLoading = ref(false);

// NOUVEAUX : États pour les dépendances
const dependencies = ref({});
const dependenciesLoading = ref(false);
const dependenciesError = ref(false);

const availableLanguages = computed(() => getAvailableLanguages());
const currentLanguage = computed(() => getCurrentLanguage());

// === PRESETS ===
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

// === VALIDATION DOCK APPS ===
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

// === PRESET HELPERS ===
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

// === NOUVEAUX : HELPERS POUR LES DÉPENDANCES ===
function getInstallStatus(dep) {
  return dep.installed?.status || 'unknown';
}

function getInstalledVersion(dep) {
  const versions = dep.installed?.versions || {};
  const versionValues = Object.values(versions);
  return versionValues.length > 0 ? versionValues[0] : null;
}

function getLatestVersion(dep) {
  return dep.latest?.version || null;
}

function getGitHubStatus(dep) {
  return dep.latest?.status || 'unknown';
}

// === NOUVEAU : GESTION DES DÉPENDANCES ===
async function loadDependencies() {
  if (dependenciesLoading.value) return;
  
  try {
    dependenciesLoading.value = true;
    dependenciesError.value = false;
    
    const response = await axios.get('/api/settings/dependencies');
    
    if (response.data.status === 'success') {
      dependencies.value = response.data.dependencies || {};
      dependenciesError.value = false;
    } else {
      dependenciesError.value = true;
      console.error('Error loading dependencies:', response.data.message);
    }
  } catch (error) {
    console.error('Error loading dependencies:', error);
    dependenciesError.value = true;
  } finally {
    dependenciesLoading.value = false;
  }
}

// === GESTION TEMPÉRATURE ===
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

// Intervalle pour mettre à jour la température
let temperatureInterval = null;

// === SCROLL PAR CLIC+DRAG (comme Modal.vue) ===
const modalContent = ref(null);
let isDragging = false;
let startY = 0;
let startScrollTop = 0;
let pointerId = null;
let hasMoved = false;

function handlePointerDown(event) {
  if (!modalContent.value) return;

  // Exclure les sliders et autres contrôles interactifs
  const isSlider = event.target.closest('input[type="range"]');
  const isButton = event.target.closest('button');
  const isInput = event.target.closest('input, select, textarea');

  if (isSlider || isButton || isInput) {
    return;
  }

  isDragging = true;
  hasMoved = false;
  pointerId = event.pointerId;
  startY = event.clientY;
  startScrollTop = modalContent.value.scrollTop;
}

function handlePointerMove(event) {
  if (!isDragging || event.pointerId !== pointerId || !modalContent.value) return;

  const deltaY = Math.abs(startY - event.clientY);

  if (deltaY > 5) {
    hasMoved = true;

    if (!modalContent.value.hasPointerCapture(event.pointerId)) {
      modalContent.value.setPointerCapture(event.pointerId);
    }

    event.preventDefault();

    const scrollDelta = startY - event.clientY;
    modalContent.value.scrollTop = startScrollTop + scrollDelta;
  }
}

function handlePointerUp(event) {
  if (event.pointerId === pointerId) {
    isDragging = false;
    pointerId = null;
    hasMoved = false;

    if (modalContent.value && modalContent.value.hasPointerCapture(event.pointerId)) {
      modalContent.value.releasePointerCapture(event.pointerId);
    }
  }
}

// === HANDLERS ===

// Handler unifié avec debounce global
let debounceTimeout = null;
function debouncedUpdate(endpoint, payload, delay = 800) {
  clearTimeout(debounceTimeout);
  debounceTimeout = setTimeout(() => updateSetting(endpoint, payload), delay);
}

// Handler principal
async function updateSetting(endpoint, payload) {
  try {
    await axios.post(`/api/settings/${endpoint}`, payload);
  } catch (error) {
    console.error(`Error updating ${endpoint}:`, error);
  }
}

// Handler dock apps
function updateDockApps() {
  const enabledApps = getEnabledAppsArray();
  debouncedUpdate('dock-apps', { enabled_apps: enabledApps }, 500);
}

// Handler volume limits (utilise le DoubleRangeSlider)
function updateVolumeLimits(limits) {
  debouncedUpdate('volume-limits', {
    alsa_min: limits.min,
    alsa_max: limits.max
  });
}

// Handler brightness avec application instantanée
let brightnessInstantTimeout = null;
let brightnessDebounceTimeout = null;

function handleBrightnessChange(value) {
  // Application immédiate
  clearTimeout(brightnessInstantTimeout);
  brightnessInstantTimeout = setTimeout(() => {
    axios.post('/api/settings/screen-brightness/apply', { brightness_on: value }).catch(console.error);
  }, 300);

  // Sauvegarde
  clearTimeout(brightnessDebounceTimeout);
  brightnessDebounceTimeout = setTimeout(() => {
    updateSetting('screen-brightness', { brightness_on: value });
  }, 1000);
}

// Handler screen timeout - Envoyer 0 pour Jamais
function setScreenTimeout(value) {
  console.log('Setting Screen timeout to:', value);
  updateSetting('screen-timeout', {
    screen_timeout_enabled: value !== 0,
    screen_timeout_seconds: value
  });
}

// Handler spotify disconnect presets
function setSpotifyDisconnect(value) {
  console.log('Setting Spotify to:', value);
  updateSetting('spotify-disconnect', { auto_disconnect_delay: value });
}

function goBack() {
  router.push('/');
}

// === CHARGEMENT INITIAL ===
async function loadAllConfigs() {
  try {
    const [volumeLimits, volumeStartup, volumeSteps, spotify, screenTimeout, brightness, dockApps] = await Promise.all([
      axios.get('/api/settings/volume-limits'),
      axios.get('/api/settings/volume-startup'),
      axios.get('/api/settings/volume-steps'),
      axios.get('/api/settings/spotify-disconnect'),
      axios.get('/api/settings/screen-timeout'),
      axios.get('/api/settings/screen-brightness'),
      axios.get('/api/settings/dock-apps')
    ]);

    // Volume limits → DoubleRangeSlider format
    if (volumeLimits.data.status === 'success') {
      config.value.volume.limits = {
        min: volumeLimits.data.limits.alsa_min || 0,
        max: volumeLimits.data.limits.alsa_max || 65
      };
    }

    // Volume startup
    if (volumeStartup.data.status === 'success') {
      config.value.volume.restore_last_volume = volumeStartup.data.config.restore_last_volume || false;
      config.value.volume.startup_volume = volumeStartup.data.config.startup_volume || 37;
    }

    // Volume steps
    if (volumeSteps.data.status === 'success') {
      config.value.volume.mobile_volume_steps = volumeSteps.data.config.mobile_volume_steps || 5;
    }

    // Spotify
    if (spotify.data.status === 'success') {
      config.value.spotify.auto_disconnect_delay = spotify.data.config.auto_disconnect_delay ?? 10.0;
    }

    // Screen - Utiliser timeout_seconds pour déterminer l'état
    if (screenTimeout.data.status === 'success') {
      config.value.screen.timeout_seconds = screenTimeout.data.config.screen_timeout_seconds ?? 10;
      // timeout_enabled dérivé de timeout_seconds
      config.value.screen.timeout_enabled = config.value.screen.timeout_seconds !== 0;
    }

    if (brightness.data.status === 'success') {
      config.value.screen.brightness_on = brightness.data.config.brightness_on || 5;
    }

    // Dock apps
    if (dockApps.data.status === 'success') {
      const enabledApps = dockApps.data.config.enabled_apps || ["librespot", "bluetooth", "roc", "multiroom", "equalizer"];

      const appsObj = {};
      ['librespot', 'bluetooth', 'roc', 'multiroom', 'equalizer'].forEach(app => {
        appsObj[app] = enabledApps.includes(app);
      });

      config.value.dock.apps = appsObj;
    }
    console.log('Spotify response:', spotify.data);
    console.log('Screen response:', screenTimeout.data);
  } catch (error) {
    console.error('Error loading configs:', error);
  }
}

// === WEBSOCKET LISTENERS ===
const wsListeners = {
  'language_changed': (msg) => i18n.handleLanguageChanged(msg.data?.language),
  'volume_limits_changed': (msg) => {
    if (msg.data?.limits) {
      config.value.volume.limits = {
        min: msg.data.limits.alsa_min || 0,
        max: msg.data.limits.alsa_max || 65
      };
    }
  },
  'volume_startup_changed': (msg) => {
    if (msg.data?.config) {
      config.value.volume.restore_last_volume = msg.data.config.restore_last_volume;
      config.value.volume.startup_volume = msg.data.config.startup_volume;
    }
  },
  'volume_steps_changed': (msg) => {
    if (msg.data?.config?.mobile_volume_steps) {
      config.value.volume.mobile_volume_steps = msg.data.config.mobile_volume_steps;
    }
  },
  'spotify_disconnect_changed': (msg) => {
    if (msg.data?.config?.auto_disconnect_delay !== undefined) {
      config.value.spotify.auto_disconnect_delay = msg.data.config.auto_disconnect_delay;
    }
  },
  // Screen timeout - Dériver timeout_enabled de timeout_seconds
  'screen_timeout_changed': (msg) => {
    if (msg.data?.config) {
      config.value.screen.timeout_seconds = msg.data.config.screen_timeout_seconds;
      config.value.screen.timeout_enabled = config.value.screen.timeout_seconds !== 0;
    }
  },
  'screen_brightness_changed': (msg) => {
    if (msg.data?.config?.brightness_on !== undefined) {
      config.value.screen.brightness_on = msg.data.config.brightness_on;
    }
  },
  'dock_apps_changed': (msg) => {
    if (msg.data?.config?.enabled_apps) {
      const enabledApps = msg.data.config.enabled_apps;
      const appsObj = {};
      ['librespot', 'bluetooth', 'roc', 'multiroom', 'equalizer'].forEach(app => {
        appsObj[app] = enabledApps.includes(app);
      });
      config.value.dock.apps = appsObj;
    }
  }
};

onMounted(async () => {
  await i18n.initializeLanguage();
  await loadAllConfigs();

  // Charger la température immédiatement
  await loadSystemTemperature();
  
  // NOUVEAU : Charger les dépendances immédiatement
  await loadDependencies();
  
  // Configurer l'intervalle pour la température (toutes les 5 secondes)
  temperatureInterval = setInterval(loadSystemTemperature, 5000);

  // Enregistrer tous les listeners WebSocket
  Object.entries(wsListeners).forEach(([eventType, handler]) => {
    on('settings', eventType, handler);
  });
});

// Nettoyer l'intervalle au démontage
onUnmounted(() => {
  if (temperatureInterval) {
    clearInterval(temperatureInterval);
  }
});
</script>

<style scoped>
.settings-view {
  background: var(--color-background-contrast);
  min-height: 100vh;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 48px var(--space-04) var(--space-07) var(--space-04);
}

.settings-modal {
  background: var(--color-background-neutral-50);
  border-radius: var(--radius-06);
  width: 100%;
  max-width: 680px;
  max-height: calc(100vh - 96px);
  display: flex;
  flex-direction: column;
  position: relative;
}

.settings-modal::before {
  content: '';
  position: absolute;
  inset: 0;
  padding: 2px;
  opacity: 0.8;
  background: var(--stroke-glass);
  border-radius: var(--radius-06);
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  z-index: -1;
  pointer-events: none;
}

.modal-header {
  background: var(--color-background-contrast);
  border-radius: var(--radius-04);
  padding: var(--space-04);
}

.back-button-wrapper {
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

.modal-header h2 {
  color: var(--color-text-contrast);
}

.modal-content {
  overflow-y: auto;
  padding: var(--space-04);
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  border-radius: var(--radius-06);
  /* Configuration pour PointerEvents - permet le scroll vertical seulement */
  touch-action: pan-y;
}

.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  padding: var(--space-06) var(--space-05) var(--space-06) var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.settings-section h2 {
  color: var(--color-text);
}

.settings-separator {
  height: 1px;
  background: var(--color-background-strong);
  margin: var(--space-02) 0;
}

/* Languages Grid */
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
  position: relative;
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
.app-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.app-group-title {
  color: var(--color-text-secondary);
  font-weight: 500;
}

.app-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.app-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-02) var(--space-02) var(--space-02) var(--space-04);
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

/* Volume */
.volume-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.volume-description,
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

/* Volume limits avec DoubleRangeSlider */
.volume-limits-control {
  display: flex;
  flex-direction: column;
}

/* Startup mode buttons */
.startup-mode-buttons {
  display: flex;
  gap: var(--space-02);
}

.startup-mode-buttons .btn {
  flex: 1;
}

/* Volume fixe sans container background */
.fixed-volume-control {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

/* Screen */
.screen-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

/* Preset buttons (timeout et disconnect) */
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

/* Spotify */
.spotify-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

/* === NOUVEAU : STYLES DÉPENDANCES === */

.dependencies-loading,
.dependencies-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-05);
  text-align: center;
}

.loading-message,
.error-message {
  color: var(--color-text-secondary);
}

.dependencies-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.dependency-item {
  background: var(--color-background-strong);
  border-radius: var(--radius-04);
  padding: var(--space-04);
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.dependency-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--space-03);
}

.dependency-info {
  flex: 1;
}

.dependency-name {
  color: var(--color-text);
  margin-bottom: var(--space-01);
}

.dependency-description {
  color: var(--color-text-secondary);
}

.dependency-status {
  flex-shrink: 0;
}

.status-badge,
.update-badge {
  padding: var(--space-01) var(--space-02);
  border-radius: var(--radius-02);
  text-align: center;
  min-width: 80px;
}

.status-ok {
  background: var(--color-background-neutral);
  color: var(--color-success);
  border: 1px solid var(--color-success);
}

.status-error {
  background: var(--color-background-neutral);
  color: var(--color-error);
  border: 1px solid var(--color-error);
}

.status-unknown {
  background: var(--color-background-neutral);
  color: var(--color-text-secondary);
  border: 1px solid var(--color-text-secondary);
}

.update-badge {
  background: var(--color-background-neutral);
  color: var(--color-warning);
  border: 1px solid var(--color-warning);
}

.dependency-versions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-03);
}

.version-info {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.version-label {
  color: var(--color-text-secondary);
}

.version-value {
  color: var(--color-text);
}

.dependency-errors {
  margin-top: var(--space-02);
}

.error-details {
  background: var(--color-background);
  border-radius: var(--radius-02);
  padding: var(--space-02);
}

.error-details summary {
  cursor: pointer;
  color: var(--color-text-secondary);
}

.error-list {
  margin: var(--space-02) 0 0 0;
  padding: 0;
  list-style: none;
}

.error-list li {
  color: var(--color-error);
  padding: var(--space-01) 0;
}

/* Informations */
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

/* Style pour température en erreur */
.text-error {
  color: var(--color-destructive);
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .settings-view {
    align-items: flex-start;
    padding: var(--space-02);
  }

  .settings-modal {
    max-height: calc(100vh - var(--space-02) * 2);
  }

  .language-grid {
    grid-template-columns: 1fr 1fr;
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

  /* Responsive pour dépendances */
  .dependency-versions {
    grid-template-columns: 1fr;
    gap: var(--space-02);
  }

  .dependencies-actions {
    flex-direction: column;
    align-items: stretch;
    gap: var(--space-02);
  }
}

/* iOS */
.ios-app .settings-modal {
  margin-top: 48px;
  max-height: calc(100vh - 64px);
}

/* Scrollbar hidden */
::-webkit-scrollbar {
  display: none;
}
</style>