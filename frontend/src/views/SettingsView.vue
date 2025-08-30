<!-- frontend/src/views/SettingsView.vue - Version refactorisée avec application immédiate -->
<template>
  <div class="settings-view">
    <!-- Header avec retour -->
    <div class="settings-header">
      <div class="back-button-wrapper">
        <IconButton icon="caretLeft" variant="dark" @click="goBack" />
        <h1 class="heading-1">{{ $t('Configuration') }}</h1>
      </div>
    </div>

    <!-- Contenu des paramètres -->
    <div class="settings-content">
      <!-- Section Volume -->
      <section class="settings-section">
        <h2 class="heading-2">{{ $t('Volume') }}</h2>
        
        <div class="volume-settings">
          <!-- Toggle pour activer/désactiver les limites -->
          <div class="volume-limits-toggle">
            <Toggle 
              v-model="volumeConfig.limits_enabled" 
              :title="$t('Limites de volume')"
              @change="handleLimitsToggle"
            />
            <div class="toggle-description text-mono">
              {{ $t('Restreindre la plage de volume utilisable') }}
            </div>
          </div>

          <!-- Configuration des limites (visible si activé) -->
          <div v-if="volumeConfig.limits_enabled" class="volume-limits">
            <div class="limit-group">
              <label class="text-mono">{{ $t('Volume minimum') }}</label>
              <div class="limit-control">
                <RangeSlider 
                  v-model="volumeConfig.alsa_min" 
                  :min="0" 
                  :max="maxMinVolume" 
                  :step="1"
                  @input="handleMinVolumeChange"
                  class="limit-slider" 
                />
                <span class="limit-value text-mono">{{ volumeConfig.alsa_min }}</span>
              </div>
            </div>

            <div class="limit-group">
              <label class="text-mono">{{ $t('Volume maximum') }}</label>
              <div class="limit-control">
                <RangeSlider 
                  v-model="volumeConfig.alsa_max" 
                  :min="minMaxVolume" 
                  :max="100" 
                  :step="1"
                  @input="handleMaxVolumeChange"
                  class="limit-slider" 
                />
                <span class="limit-value text-mono">{{ volumeConfig.alsa_max }}</span>
              </div>
            </div>

            <!-- Aperçu du range -->
            <div class="volume-preview">
              <div class="preview-label text-mono">{{ $t('Plage de volume résultante') }}</div>
              <div class="preview-range">
                <div class="range-bar">
                  <div 
                    class="range-fill" 
                    :style="{ 
                      left: `${volumeConfig.alsa_min}%`, 
                      width: `${volumeConfig.alsa_max - volumeConfig.alsa_min}%` 
                    }"
                  ></div>
                </div>
                <div class="range-labels text-mono">
                  <span>{{ volumeConfig.alsa_min }}%</span>
                  <span>{{ volumeConfig.alsa_max }}%</span>
                </div>
              </div>
            </div>
          </div>

          <!-- Messages de validation -->
          <div v-if="volumeValidation.error" class="validation-error">
            {{ volumeValidation.message }}
          </div>

          <!-- Séparateur -->
          <div class="settings-separator"></div>

          <!-- Volume au démarrage -->
          <div class="startup-volume-settings">
            <h3 class="heading-2">{{ $t('Volume au démarrage') }}</h3>
            
            <!-- Mode selection -->
            <div class="startup-mode-selector">
              <button 
                @click="setStartupMode(false)"
                :class="['mode-button', { active: !startupVolumeConfig.restore_last_volume }]"
                class="button-interactive"
              >
                <div class="mode-content">
                  <div class="mode-title heading-2">{{ $t('Volume fixe') }}</div>
                  <div class="mode-description text-mono">{{ $t('Utilise toujours le même volume') }}</div>
                  <div v-if="!startupVolumeConfig.restore_last_volume" class="active-indicator"></div>
                </div>
              </button>

              <button 
                @click="setStartupMode(true)"
                :class="['mode-button', { active: startupVolumeConfig.restore_last_volume }]"
                class="button-interactive"
              >
                <div class="mode-content">
                  <div class="mode-title heading-2">{{ $t('Restaurer le dernier') }}</div>
                  <div class="mode-description text-mono">{{ $t('Reprend le volume avant redémarrage') }}</div>
                  <div v-if="startupVolumeConfig.restore_last_volume" class="active-indicator"></div>
                </div>
              </button>
            </div>

            <!-- Volume fixe slider (seulement visible en mode fixe) -->
            <div v-if="!startupVolumeConfig.restore_last_volume" class="fixed-volume-control">
              <label class="text-mono">{{ $t('Volume fixe au démarrage') }}</label>
              <div class="startup-control">
                <RangeSlider 
                  v-model="startupVolumeConfig.startup_volume" 
                  :min="0" 
                  :max="100" 
                  :step="1"
                  @input="handleStartupVolumeChange"
                  class="startup-slider" 
                />
                <span class="startup-value text-mono">{{ startupVolumeConfig.startup_volume }}%</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- Section Spotify -->
      <section class="settings-section">
        <h2 class="heading-2">{{ $t('Spotify') }}</h2>
        
        <div class="spotify-settings">
          <div class="disconnect-timer">
            <label class="text-mono">{{ $t('Déconnexion automatique après pause') }}</label>
            <div class="timer-control">
              <RangeSlider 
                v-model="spotifyConfig.auto_disconnect_delay" 
                :min="1" 
                :max="300" 
                :step="1"
                @input="handleSpotifyDelayChange"
                class="timer-slider" 
              />
              <span class="timer-value text-mono">{{ formatDuration(spotifyConfig.auto_disconnect_delay) }}</span>
            </div>
            <div class="timer-description text-mono">
              {{ $t('Spotify se déconnecte automatiquement après ce délai en pause') }}
            </div>
          </div>

          <!-- Messages de validation spotify -->
          <div v-if="spotifyValidation.error" class="validation-error">
            {{ spotifyValidation.message }}
          </div>
        </div>
      </section>

      <!-- Section Écran -->
      <section class="settings-section">
        <h2 class="heading-2">{{ $t('Écran') }}</h2>
        
        <div class="screen-settings">
          <!-- Timeout d'écran -->
          <div class="timeout-control">
            <label class="text-mono">{{ $t('Mise en veille automatique') }}</label>
            <div class="timer-control">
              <RangeSlider 
                v-model="screenConfig.screen_timeout_seconds" 
                :min="3" 
                :max="3600" 
                :step="1"
                @input="handleScreenTimeoutChange"
                class="timer-slider" 
              />
              <span class="timer-value text-mono">{{ formatDuration(screenConfig.screen_timeout_seconds) }}</span>
            </div>
            <div class="timer-description text-mono">
              {{ $t('L\'écran se met en veille après ce délai d\'inactivité') }}
            </div>
          </div>

          <!-- Séparateur -->
          <div class="settings-separator"></div>

          <!-- Luminosité -->
          <div class="brightness-controls">
            <h3 class="heading-2">{{ $t('Luminosité') }}</h3>
            
            <div class="brightness-group">
              <label class="text-mono">{{ $t('Écran allumé') }}</label>
              <div class="brightness-control">
                <RangeSlider 
                  v-model="screenConfig.brightness_on" 
                  :min="1" 
                  :max="10" 
                  :step="1"
                  @input="handleBrightnessChange"
                  class="brightness-slider" 
                />
                <span class="brightness-value text-mono">{{ screenConfig.brightness_on }}</span>
              </div>
              <div class="brightness-description text-mono">
                {{ $t('Appliqué instantanément pendant l\'utilisation') }}
              </div>
            </div>
          </div>

          <!-- Messages de validation screen -->
          <div v-if="screenValidation.error" class="validation-error">
            {{ screenValidation.message }}
          </div>
        </div>
      </section>
      
      <!-- Section Langue -->
      <section class="settings-section">
        <h2 class="heading-2">{{ $t('Langue') }}</h2>
        
        <div class="language-selector">
          <button 
            v-for="language in availableLanguages" 
            :key="language.code"
            @click="changeLanguage(language.code)"
            :class="['language-button', { active: currentLanguage === language.code }]"
            class="button-interactive"
          >
            <span class="language-flag">{{ language.flag }}</span>
            <span class="language-name heading-2">{{ language.name }}</span>
            <div v-if="currentLanguage === language.code" class="active-indicator"></div>
          </button>
        </div>
      </section>

      <!-- Informations version -->
      <section class="settings-section">
        <h2 class="heading-2">{{ $t('Informations') }}</h2>
        
        <div class="info-grid">
          <div class="info-item">
            <span class="info-label text-mono">Version</span>
            <span class="info-value text-mono">1.0.0</span>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { useI18n } from '@/services/i18n';
import { i18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import IconButton from '@/components/ui/IconButton.vue';
import Toggle from '@/components/ui/Toggle.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import axios from 'axios';

const router = useRouter();
const { setLanguage, getAvailableLanguages, getCurrentLanguage } = useI18n();
const { on } = useWebSocket();

// Configuration volume avec toggle
const volumeConfig = ref({
  alsa_min: 0,
  alsa_max: 65,
  limits_enabled: true
});

// Configuration volume au démarrage
const startupVolumeConfig = ref({
  startup_volume: 37,
  restore_last_volume: false
});

// Configuration Spotify
const spotifyConfig = ref({
  auto_disconnect_delay: 10.0
});

// Configuration écran
const screenConfig = ref({
  screen_timeout_seconds: 10,
  brightness_on: 5
});

// Langues disponibles
const availableLanguages = computed(() => getAvailableLanguages());
const currentLanguage = computed(() => getCurrentLanguage());

// Validation des limites de volume
const maxMinVolume = computed(() => Math.max(0, volumeConfig.value.alsa_max - 10));
const minMaxVolume = computed(() => Math.min(100, volumeConfig.value.alsa_min + 10));

const volumeValidation = computed(() => {
  if (!volumeConfig.value.limits_enabled) return { error: false, message: '' };
  
  const min = volumeConfig.value.alsa_min;
  const max = volumeConfig.value.alsa_max;
  const range = max - min;
  
  if (range < 10) {
    return {
      error: true,
      message: 'La plage de volume doit être d\'au moins 10'
    };
  }
  
  if (min < 0 || max > 100) {
    return {
      error: true,
      message: 'Les limites doivent être entre 0 et 100'
    };
  }
  
  return { error: false, message: '' };
});

// Validation Spotify
const spotifyValidation = computed(() => {
  const delay = spotifyConfig.value.auto_disconnect_delay;
  
  if (delay < 1 || delay > 300) {
    return {
      error: true,
      message: 'Le délai doit être entre 1 seconde et 5 minutes'
    };
  }
  
  return { error: false, message: '' };
});

// Validation Screen
const screenValidation = computed(() => {
  const timeout = screenConfig.value.screen_timeout_seconds;
  const brightnessOn = screenConfig.value.brightness_on;
  
  if (timeout < 3 || timeout > 3600) {
    return {
      error: true,
      message: 'Le timeout doit être entre 3 secondes et 60 minutes'
    };
  }
  
  if (brightnessOn < 1 || brightnessOn > 10) {
    return {
      error: true,
      message: 'La luminosité doit être entre 1 et 10'
    };
  }
  
  return { error: false, message: '' };
});

// Formatage des durées
function formatDuration(seconds) {
  if (seconds < 60) {
    return `${seconds}s`;
  } else if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = seconds % 60;
    return remainingSeconds > 0 ? `${minutes}m ${remainingSeconds}s` : `${minutes}m`;
  } else {
    const hours = Math.floor(seconds / 3600);
    const remainingMinutes = Math.floor((seconds % 3600) / 60);
    return remainingMinutes > 0 ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
  }
}

// Debounce global pour éviter trop d'appels
let debounceTimeout = null;
function debounce(func, delay = 500) {
  clearTimeout(debounceTimeout);
  debounceTimeout = setTimeout(func, delay);
}

// Handlers avec application immédiate

// Volume limits toggle
async function handleLimitsToggle(enabled) {
  console.log('Volume limits toggled:', enabled);
  
  try {
    await axios.post('/api/settings/volume-limits/toggle', { enabled });
  } catch (error) {
    console.error('Error toggling volume limits:', error);
  }
}

// Volume limits changes
function handleMinVolumeChange(newMin) {
  if (newMin + 10 > volumeConfig.value.alsa_max) {
    volumeConfig.value.alsa_max = Math.min(100, newMin + 10);
  }
  
  debounce(() => applyVolumeLimits(), 800);
}

function handleMaxVolumeChange(newMax) {
  if (newMax - 10 < volumeConfig.value.alsa_min) {
    volumeConfig.value.alsa_min = Math.max(0, newMax - 10);
  }
  
  debounce(() => applyVolumeLimits(), 800);
}

async function applyVolumeLimits() {
  if (volumeValidation.value.error) return;
  
  try {
    await axios.post('/api/settings/volume-limits', {
      alsa_min: volumeConfig.value.alsa_min,
      alsa_max: volumeConfig.value.alsa_max
    });
    console.log('Volume limits applied automatically');
  } catch (error) {
    console.error('Error applying volume limits:', error);
  }
}

// Startup volume changes
function setStartupMode(restoreMode) {
  startupVolumeConfig.value.restore_last_volume = restoreMode;
  applyStartupVolumeConfig();
}

function handleStartupVolumeChange() {
  debounce(() => applyStartupVolumeConfig(), 800);
}

async function applyStartupVolumeConfig() {
  try {
    await axios.post('/api/settings/volume-startup', {
      startup_volume: startupVolumeConfig.value.startup_volume,
      restore_last_volume: startupVolumeConfig.value.restore_last_volume
    });
    console.log('Startup volume config applied automatically');
  } catch (error) {
    console.error('Error applying startup volume config:', error);
  }
}

// Spotify changes
function handleSpotifyDelayChange() {
  debounce(() => applySpotifyConfig(), 800);
}

async function applySpotifyConfig() {
  if (spotifyValidation.value.error) return;
  
  try {
    await axios.post('/api/settings/spotify-disconnect', {
      auto_disconnect_delay: spotifyConfig.value.auto_disconnect_delay
    });
    console.log('Spotify config applied automatically');
  } catch (error) {
    console.error('Error applying Spotify config:', error);
  }
}

// Screen timeout changes
function handleScreenTimeoutChange() {
  debounce(() => applyScreenTimeoutConfig(), 800);
}

async function applyScreenTimeoutConfig() {
  if (screenValidation.value.error) return;
  
  try {
    await axios.post('/api/settings/screen-timeout', {
      screen_timeout_seconds: screenConfig.value.screen_timeout_seconds
    });
    console.log('Screen timeout applied automatically');
  } catch (error) {
    console.error('Error applying screen timeout:', error);
  }
}

// Brightness changes (application instantanée + sauvegarde avec debounce)
let brightnessInstantTimeout = null;
let brightnessDebounceTimeout = null;

function handleBrightnessChange(brightnessValue) {
  // Application immédiate (300ms debounce)
  clearTimeout(brightnessInstantTimeout);
  brightnessInstantTimeout = setTimeout(async () => {
    try {
      await axios.post('/api/settings/screen-brightness/apply', {
        brightness_on: brightnessValue
      });
    } catch (error) {
      console.error('Error applying brightness instantly:', error);
    }
  }, 300);
  
  // Sauvegarde définitive (1 seconde debounce)
  clearTimeout(brightnessDebounceTimeout);
  brightnessDebounceTimeout = setTimeout(async () => {
    try {
      await axios.post('/api/settings/screen-brightness', {
        brightness_on: brightnessValue
      });
      console.log('Brightness saved automatically');
    } catch (error) {
      console.error('Error saving brightness:', error);
    }
  }, 1000);
}

// Langue (application immédiate)
async function changeLanguage(languageCode) {
  await setLanguage(languageCode);
}

function goBack() {
  router.push('/');
}

// Chargement initial
async function loadAllConfigs() {
  try {
    const [volumeLimitsResponse, startupVolumeResponse, spotifyResponse, screenTimeoutResponse, brightnessResponse] = await Promise.all([
      axios.get('/api/settings/volume-limits'),
      axios.get('/api/settings/volume-startup'),
      axios.get('/api/settings/spotify-disconnect'),
      axios.get('/api/settings/screen-timeout'),
      axios.get('/api/settings/screen-brightness')
    ]);
    
    // Volume limits
    if (volumeLimitsResponse.data.status === 'success') {
      const limits = volumeLimitsResponse.data.limits;
      volumeConfig.value = {
        alsa_min: limits.alsa_min,
        alsa_max: limits.alsa_max,
        limits_enabled: limits.limits_enabled
      };
    }
    
    // Startup volume
    if (startupVolumeResponse.data.status === 'success') {
      const config = startupVolumeResponse.data.config;
      startupVolumeConfig.value = {
        startup_volume: config.startup_volume,
        restore_last_volume: config.restore_last_volume
      };
    }
    
    // Spotify
    if (spotifyResponse.data.status === 'success') {
      const config = spotifyResponse.data.config;
      spotifyConfig.value = {
        auto_disconnect_delay: config.auto_disconnect_delay
      };
    }
    
    // Screen timeout
    if (screenTimeoutResponse.data.status === 'success') {
      const config = screenTimeoutResponse.data.config;
      screenConfig.value.screen_timeout_seconds = config.screen_timeout_seconds;
    }
    
    // Screen brightness
    if (brightnessResponse.data.status === 'success') {
      const config = brightnessResponse.data.config;
      screenConfig.value.brightness_on = config.brightness_on;
    }
    
    console.log('All configurations loaded');
    
  } catch (error) {
    console.error('Error loading configurations:', error);
  }
}

// Initialisation et WebSocket
onMounted(async () => {
  await i18n.initializeLanguage();
  await loadAllConfigs();
  
  // WebSocket listeners
  on('settings', 'language_changed', (message) => {
    if (message.data?.language) {
      i18n.handleLanguageChanged(message.data.language);
    }
  });
  
  on('settings', 'volume_limits_changed', (message) => {
    if (message.data?.limits) {
      const newLimits = message.data.limits;
      volumeConfig.value.alsa_min = newLimits.alsa_min;
      volumeConfig.value.alsa_max = newLimits.alsa_max;
    }
  });
  
  on('settings', 'volume_limits_toggled', (message) => {
    if (message.data && typeof message.data.limits_enabled === 'boolean') {
      volumeConfig.value.limits_enabled = message.data.limits_enabled;
    }
  });
  
  on('settings', 'volume_startup_changed', (message) => {
    if (message.data?.config) {
      const newConfig = message.data.config;
      startupVolumeConfig.value = {
        startup_volume: newConfig.startup_volume,
        restore_last_volume: newConfig.restore_last_volume
      };
    }
  });
  
  on('settings', 'spotify_disconnect_changed', (message) => {
    if (message.data?.config) {
      spotifyConfig.value.auto_disconnect_delay = message.data.config.auto_disconnect_delay;
    }
  });
  
  on('settings', 'screen_timeout_changed', (message) => {
    if (message.data?.config) {
      screenConfig.value.screen_timeout_seconds = message.data.config.screen_timeout_seconds;
    }
  });
  
  on('settings', 'screen_brightness_changed', (message) => {
    if (message.data?.config) {
      screenConfig.value.brightness_on = message.data.config.brightness_on;
    }
  });
});
</script>

<style scoped>
.settings-view {
  background: var(--color-background);
  min-height: 100%;
  padding: var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.settings-header {
  background: var(--color-background-contrast);
  border-radius: var(--radius-04);
  padding: var(--space-04);
}

.back-button-wrapper {
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

.settings-header h1 {
  color: var(--color-text-contrast);
}

.settings-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  padding: var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.settings-section h2 {
  color: var(--color-text);
}

/* === SÉPARATEUR === */

.settings-separator {
  height: 1px;
  background: var(--color-background-strong);
  margin: var(--space-02) 0;
}

/* === VOLUME TOGGLE ET LIMITES === */

.volume-settings {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.volume-limits-toggle {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.toggle-description {
  color: var(--color-text-secondary);
  font-size: var(--font-size-small);
}

.volume-limits {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.limit-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.limit-group label {
  color: var(--color-text-secondary);
}

.limit-control {
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

.limit-slider {
  flex: 1;
}

.limit-value {
  color: var(--color-text);
  min-width: 40px;
  text-align: right;
}

.volume-preview {
  background: var(--color-background-strong);
  border-radius: var(--radius-04);
  padding: var(--space-04);
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.preview-label {
  color: var(--color-text-secondary);
}

.preview-range {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.range-bar {
  height: 8px;
  background: var(--color-background);
  border-radius: var(--radius-full);
  position: relative;
  overflow: hidden;
}

.range-fill {
  position: absolute;
  top: 0;
  height: 100%;
  background: var(--color-brand);
  border-radius: var(--radius-full);
  transition: all var(--transition-fast);
}

.range-labels {
  display: flex;
  justify-content: space-between;
  color: var(--color-text-secondary);
}

/* === VOLUME AU DÉMARRAGE === */

.startup-volume-settings {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.startup-mode-selector {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.mode-button {
  display: flex;
  align-items: center;
  padding: var(--space-04);
  background: var(--color-background-strong);
  border: 2px solid transparent;
  border-radius: var(--radius-04);
  cursor: pointer;
  transition: all var(--transition-fast);
  position: relative;
  width: 100%;
  text-align: left;
}

.mode-button:hover {
  background: var(--color-background);
  border-color: var(--color-background-glass);
}

.mode-button.active {
  background: var(--color-background);
  border-color: var(--color-brand);
}

.mode-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  flex: 1;
  position: relative;
}

.mode-title {
  color: var(--color-text);
}

.mode-description {
  color: var(--color-text-secondary);
}

.fixed-volume-control {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  padding: var(--space-04);
  background: var(--color-background-strong);
  border-radius: var(--radius-04);
}

.fixed-volume-control label {
  color: var(--color-text-secondary);
}

.startup-control {
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

.startup-slider {
  flex: 1;
}

.startup-value {
  color: var(--color-text);
  min-width: 40px;
  text-align: right;
}

/* === STYLES SPOTIFY, SCREEN ET LUMINOSITÉ === */

.spotify-settings,
.screen-settings {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.disconnect-timer,
.timeout-control {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.brightness-controls {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.brightness-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.disconnect-timer label,
.timeout-control label,
.brightness-group label {
  color: var(--color-text-secondary);
}

.timer-control,
.brightness-control {
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

.timer-slider,
.brightness-slider {
  flex: 1;
}

.timer-value,
.brightness-value {
  color: var(--color-text);
  min-width: 60px;
  text-align: right;
}

.timer-description,
.brightness-description {
  color: var(--color-text-tertiary);
  font-size: var(--font-size-small);
}

.validation-error {
  color: var(--color-error);
  font-size: var(--font-size-mono);
  padding: var(--space-02) var(--space-03);
  background: rgba(244, 67, 54, 0.1);
  border-radius: var(--radius-03);
  border-left: 3px solid var(--color-error);
}

/* === SECTION LANGUE === */

.language-selector {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.language-button {
  display: flex;
  align-items: center;
  gap: var(--space-04);
  padding: var(--space-04);
  background: var(--color-background-strong);
  border: 2px solid transparent;
  border-radius: var(--radius-04);
  cursor: pointer;
  transition: all var(--transition-fast);
  position: relative;
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
  font-size: 24px;
  width: 32px;
  text-align: center;
}

.language-name {
  flex: 1;
  color: var(--color-text);
}

.active-indicator {
  width: 8px;
  height: 8px;
  background: var(--color-brand);
  border-radius: var(--radius-full);
}

/* === GRILLE D'INFORMATIONS === */

.info-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-02);
}

.info-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-03) var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-background-strong);
}

.info-label {
  color: var(--color-text-secondary);
}

.info-value {
  color: var(--color-text);
}

/* === RESPONSIVE === */

@media (max-aspect-ratio: 4/3) {
  .settings-view {
    padding: var(--space-04);
  }
  
  .language-selector {
    gap: var(--space-03);
  }
  
  .language-button {
    padding: var(--space-05) var(--space-04);
  }
  
  .volume-limits {
    gap: var(--space-05);
  }
  
  .limit-control,
  .timer-control,
  .brightness-control {
    gap: var(--space-04);
  }

  .startup-mode-selector {
    gap: var(--space-03);
  }

  .mode-button {
    padding: var(--space-05) var(--space-04);
  }
}

.ios-app .settings-view {
  padding-top: var(--space-08);
}
</style>