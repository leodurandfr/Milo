<!-- frontend/src/views/SettingsView.vue - Structure simplifiée avec toggle écran -->
<template>
  <div class="settings-view">
    <!-- Header -->
    <div class="settings-header">
      <div class="back-button-wrapper">
        <IconButton icon="caretLeft" variant="dark" @click="goBack" />
        <h1 class="heading-1">{{ $t('Configuration') }}</h1>
      </div>
    </div>

    <!-- Contenu -->
    <div class="settings-content">
      <!-- Interface -->
      <section class="settings-section">
        <h2 class="heading-2">{{ $t('Interface') }}</h2>
        
        <!-- Applications du dock -->
        <div class="dock-settings">
          <h3 class="heading-2">{{ $t('Applications') }}</h3>
          
          <!-- Sources audio -->
          <div class="app-group">
            <h4 class="app-group-title text-mono">{{ $t('Sources audio') }}</h4>
            
            <div class="app-toggles">
              <Toggle 
                v-model="config.dock.apps.librespot" 
                title="Spotify"
                :disabled="!canDisableAudioSource('librespot')"
                @change="updateDockApps"
              />
              <Toggle 
                v-model="config.dock.apps.bluetooth" 
                title="Bluetooth"
                :disabled="!canDisableAudioSource('bluetooth')"
                @change="updateDockApps"
              />
              <Toggle 
                v-model="config.dock.apps.roc" 
                title="Audio macOS"
                :disabled="!canDisableAudioSource('roc')"
                @change="updateDockApps"
              />
            </div>
          </div>

          <!-- Fonctionnalités -->
          <div class="app-group">
            <h4 class="app-group-title text-mono">{{ $t('Fonctionnalités') }}</h4>
            
            <div class="app-toggles">
              <Toggle 
                v-model="config.dock.apps.multiroom" 
                title="Multiroom"
                @change="updateDockApps"
              />
              <Toggle 
                v-model="config.dock.apps.equalizer" 
                title="Égaliseur"
                @change="updateDockApps"
              />
            </div>
          </div>

          <div class="settings-separator"></div>

          <!-- Contrôles volume mobile -->
          <div class="volume-controls-settings">
            <h3 class="heading-2">{{ $t('Contrôles volume mobile') }}</h3>
            <div class="controls-description text-mono">
              {{ $t('Incrémentation des boutons de volume via le dock en mobile') }}
            </div>
            
            <div class="volume-steps-control">
              <label class="text-mono">{{ $t('Incrément') }}</label>
              <div class="steps-control">
                <RangeSlider 
                  v-model="config.volume.mobile_volume_steps" 
                  :min="1" 
                  :max="10" 
                  :step="1"
                  @input="debouncedUpdate('volume-steps', { mobile_volume_steps: $event })"
                />
                <span class="steps-value text-mono">{{ config.volume.mobile_volume_steps }}%</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- Volume -->
      <section class="settings-section">
        <h2 class="heading-2">{{ $t('Volume') }}</h2>
        
        <div class="volume-settings">
          <!-- Toggle limites -->
          <Toggle 
            v-model="config.volume.limits_enabled" 
            :title="$t('Limites de volume')"
            @change="updateVolumeLimitsToggle"
          />

          <!-- Sliders limites (seulement si activées) -->
          <div v-if="config.volume.limits_enabled" class="volume-limits">
            <div class="limit-group">
              <label class="text-mono">{{ $t('Volume minimum') }}</label>
              <div class="limit-control">
                <RangeSlider 
                  v-model="config.volume.alsa_min" 
                  :min="0" 
                  :max="Math.max(0, config.volume.alsa_max - 10)" 
                  :step="1"
                  @input="updateVolumeLimits"
                />
                <span class="limit-value text-mono">{{ config.volume.alsa_min }}</span>
              </div>
            </div>

            <div class="limit-group">
              <label class="text-mono">{{ $t('Volume maximum') }}</label>
              <div class="limit-control">
                <RangeSlider 
                  v-model="config.volume.alsa_max" 
                  :min="Math.min(100, config.volume.alsa_min + 10)" 
                  :max="100" 
                  :step="1"
                  @input="updateVolumeLimits"
                />
                <span class="limit-value text-mono">{{ config.volume.alsa_max }}</span>
              </div>
            </div>

            <!-- Preview range -->
            <div class="volume-preview">
              <div class="preview-label text-mono">{{ $t('Plage de volume résultante') }}</div>
              <div class="range-bar">
                <div 
                  class="range-fill" 
                  :style="{ 
                    left: `${config.volume.alsa_min}%`, 
                    width: `${config.volume.alsa_max - config.volume.alsa_min}%` 
                  }"
                ></div>
              </div>
              <div class="range-labels text-mono">
                <span>{{ config.volume.alsa_min }}%</span>
                <span>{{ config.volume.alsa_max }}%</span>
              </div>
            </div>
          </div>

          <div class="settings-separator"></div>

          <!-- Volume au démarrage -->
          <div class="startup-volume-settings">
            <h3 class="heading-2">{{ $t('Volume au démarrage') }}</h3>
            
            <div class="startup-mode-selector">
              <button 
                @click="updateSetting('volume-startup', { startup_volume: config.volume.startup_volume, restore_last_volume: false })"
                :class="['mode-button', { active: !config.volume.restore_last_volume }]"
              >
                <div class="mode-content">
                  <div class="mode-title heading-2">{{ $t('Volume fixe') }}</div>
                  <div class="mode-description text-mono">{{ $t('Utilise toujours le même volume') }}</div>
                  <div v-if="!config.volume.restore_last_volume" class="active-indicator"></div>
                </div>
              </button>

              <button 
                @click="updateSetting('volume-startup', { startup_volume: config.volume.startup_volume, restore_last_volume: true })"
                :class="['mode-button', { active: config.volume.restore_last_volume }]"
              >
                <div class="mode-content">
                  <div class="mode-title heading-2">{{ $t('Restaurer le dernier') }}</div>
                  <div class="mode-description text-mono">{{ $t('Reprend le volume avant redémarrage') }}</div>
                  <div v-if="config.volume.restore_last_volume" class="active-indicator"></div>
                </div>
              </button>
            </div>

            <!-- Volume fixe slider -->
            <div v-if="!config.volume.restore_last_volume" class="fixed-volume-control">
              <label class="text-mono">{{ $t('Volume fixe au démarrage') }}</label>
              <div class="startup-control">
                <RangeSlider 
                  v-model="config.volume.startup_volume" 
                  :min="0" 
                  :max="100" 
                  :step="1"
                  @input="debouncedUpdate('volume-startup', { startup_volume: $event, restore_last_volume: false })"
                />
                <span class="startup-value text-mono">{{ config.volume.startup_volume }}%</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- Spotify -->
      <section class="settings-section">
        <h2 class="heading-2">{{ $t('Spotify') }}</h2>
        
        <div class="disconnect-timer">
          <label class="text-mono">{{ $t('Déconnexion automatique après pause') }}</label>
          <div class="timer-control">
            <RangeSlider 
              v-model="config.spotify.auto_disconnect_delay" 
              :min="1" 
              :max="300" 
              :step="1"
              @input="debouncedUpdate('spotify-disconnect', { auto_disconnect_delay: $event })"
            />
            <span class="timer-value text-mono">{{ formatDuration(config.spotify.auto_disconnect_delay) }}</span>
          </div>
          <div class="timer-description text-mono">
            {{ $t('Spotify se déconnecte automatiquement après ce délai en pause') }}
          </div>
        </div>
      </section>

      <!-- Écran -->
      <section class="settings-section">
        <h2 class="heading-2">{{ $t('Écran') }}</h2>
        
        <div class="screen-settings">
          <!-- Toggle mise en veille -->
          <Toggle 
            v-model="config.screen.timeout_enabled" 
            :title="$t('Mise en veille automatique')"
            @change="updateScreenTimeout"
          />

          <!-- Timeout (seulement si activé) -->
          <div v-if="config.screen.timeout_enabled" class="timeout-control">
            <label class="text-mono">{{ $t('Délai de mise en veille') }}</label>
            <div class="timer-control">
              <RangeSlider 
                v-model="config.screen.screen_timeout_seconds" 
                :min="3" 
                :max="3600" 
                :step="1"
                @input="updateScreenTimeout"
              />
              <span class="timer-value text-mono">{{ formatDuration(config.screen.screen_timeout_seconds) }}</span>
            </div>
          </div>

          <div class="settings-separator"></div>

          <!-- Luminosité -->
          <div class="brightness-controls">
            <h3 class="heading-2">{{ $t('Luminosité') }}</h3>
            
            <div class="brightness-group">
              <label class="text-mono">{{ $t('Écran allumé') }}</label>
              <div class="brightness-control">
                <RangeSlider 
                  v-model="config.screen.brightness_on" 
                  :min="1" 
                  :max="10" 
                  :step="1"
                  @input="handleBrightnessChange"
                />
                <span class="brightness-value text-mono">{{ config.screen.brightness_on }}</span>
              </div>
              <div class="brightness-description text-mono">
                {{ $t('Appliqué instantanément pendant l\'utilisation') }}
              </div>
            </div>
          </div>
        </div>
      </section>
      
      <!-- Langue -->
      <section class="settings-section">
        <h2 class="heading-2">{{ $t('Langue') }}</h2>
        
        <div class="language-selector">
          <button 
            v-for="language in availableLanguages" 
            :key="language.code"
            @click="updateSetting('language', { language: language.code })"
            :class="['language-button', { active: currentLanguage === language.code }]"
          >
            <span class="language-flag">{{ language.flag }}</span>
            <span class="language-name heading-2">{{ language.name }}</span>
            <div v-if="currentLanguage === language.code" class="active-indicator"></div>
          </button>
        </div>
      </section>

      <!-- Informations -->
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

// Configuration unifiée 
const config = ref({
  volume: {
    limits_enabled: true,
    alsa_min: 0,
    alsa_max: 65,
    restore_last_volume: false,
    startup_volume: 37,
    mobile_volume_steps: 5
  },
  spotify: {
    auto_disconnect_delay: 10.0
  },
  screen: {
    timeout_enabled: true,
    screen_timeout_seconds: 10,
    brightness_on: 5
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

const availableLanguages = computed(() => getAvailableLanguages());
const currentLanguage = computed(() => getCurrentLanguage());

// === VALIDATION DOCK APPS ===
function canDisableAudioSource(sourceId) {
  // Compter les sources audio actuellement activées
  const audioSources = ['librespot', 'bluetooth', 'roc'];
  const enabledAudioSources = audioSources.filter(source => 
    config.value.dock.apps[source] && source !== sourceId
  );
  
  // On peut désactiver seulement s'il reste au moins une autre source activée
  return enabledAudioSources.length > 0;
}

function getEnabledAppsArray() {
  return Object.keys(config.value.dock.apps).filter(app => config.value.dock.apps[app]);
}

// === HANDLERS ===
function formatDuration(seconds) {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = seconds % 60;
    return remainingSeconds > 0 ? `${minutes}m ${remainingSeconds}s` : `${minutes}m`;
  }
  const hours = Math.floor(seconds / 3600);
  const remainingMinutes = Math.floor((seconds % 3600) / 60);
  return remainingMinutes > 0 ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
}

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

// Handler spécialisé pour dock apps
function updateDockApps() {
  const enabledApps = getEnabledAppsArray();
  debouncedUpdate('dock-apps', { enabled_apps: enabledApps }, 500);
}

// Handler spécialisé pour volume limits toggle (avec auto-reset à 0-100)
function updateVolumeLimitsToggle(enabled) {
  if (!enabled) {
    // Désactiver les limites -> forcer 0-100 localement aussi
    config.value.volume.alsa_min = 0;
    config.value.volume.alsa_max = 100;
  }
  updateSetting('volume-limits/toggle', { enabled });
}

// Handler spécialisé pour screen timeout (avec toggle + valeur)
function updateScreenTimeout() {
  debouncedUpdate('screen-timeout', {
    screen_timeout_enabled: config.value.screen.timeout_enabled,
    screen_timeout_seconds: config.value.screen.screen_timeout_seconds
  }, 500);
}

// Handler volume limits
function updateVolumeLimits() {
  // Auto-ajustement des limites
  if (config.value.volume.alsa_min + 10 > config.value.volume.alsa_max) {
    config.value.volume.alsa_max = Math.min(100, config.value.volume.alsa_min + 10);
  }
  if (config.value.volume.alsa_max - 10 < config.value.volume.alsa_min) {
    config.value.volume.alsa_min = Math.max(0, config.value.volume.alsa_max - 10);
  }
  
  debouncedUpdate('volume-limits', {
    alsa_min: config.value.volume.alsa_min,
    alsa_max: config.value.volume.alsa_max
  });
}

// Brightness avec double logique (instant + save)
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
    
    // Volume
    if (volumeLimits.data.status === 'success') {
      Object.assign(config.value.volume, volumeLimits.data.limits);
    }
    
    if (volumeStartup.data.status === 'success') {
      Object.assign(config.value.volume, volumeStartup.data.config);
    }
    
    if (volumeSteps.data.status === 'success') {
      config.value.volume.mobile_volume_steps = volumeSteps.data.config.mobile_volume_steps || 5;
    }
    
    // Spotify
    if (spotify.data.status === 'success') {
      Object.assign(config.value.spotify, spotify.data.config);
    }
    
    // Screen (avec timeout_enabled)
    if (screenTimeout.data.status === 'success') {
      Object.assign(config.value.screen, screenTimeout.data.config);
    }
    
    if (brightness.data.status === 'success') {
      Object.assign(config.value.screen, brightness.data.config);
    }
    
    // Dock apps
    if (dockApps.data.status === 'success') {
      const enabledApps = dockApps.data.config.enabled_apps || ["librespot", "bluetooth", "roc", "multiroom", "equalizer"];
      
      // Convertir array vers objet pour les toggles
      const appsObj = {};
      ['librespot', 'bluetooth', 'roc', 'multiroom', 'equalizer'].forEach(app => {
        appsObj[app] = enabledApps.includes(app);
      });
      
      config.value.dock.apps = appsObj;
    }
    
  } catch (error) {
    console.error('Error loading configs:', error);
  }
}

// === WEBSOCKET LISTENERS ===
const wsListeners = {
  'language_changed': (msg) => i18n.handleLanguageChanged(msg.data?.language),
  'volume_limits_changed': (msg) => Object.assign(config.value.volume, msg.data?.limits || {}),
  'volume_limits_toggled': (msg) => {
    config.value.volume.limits_enabled = msg.data?.limits_enabled;
    // Mettre à jour les limites locales aussi si elles sont dans la réponse
    if (msg.data?.limits) {
      Object.assign(config.value.volume, msg.data.limits);
    }
  },
  'volume_startup_changed': (msg) => Object.assign(config.value.volume, msg.data?.config || {}),
  'spotify_disconnect_changed': (msg) => Object.assign(config.value.spotify, msg.data?.config || {}),
  'screen_timeout_changed': (msg) => Object.assign(config.value.screen, msg.data?.config || {}),
  'screen_brightness_changed': (msg) => Object.assign(config.value.screen, msg.data?.config || {}),
  'dock_apps_changed': (msg) => {
    if (msg.data?.config?.enabled_apps) {
      const enabledApps = msg.data.config.enabled_apps;
      const appsObj = {};
      ['librespot', 'bluetooth', 'roc', 'multiroom', 'equalizer'].forEach(app => {
        appsObj[app] = enabledApps.includes(app);
      });
      config.value.dock.apps = appsObj;
    }
  },
  'volume_steps_changed': (msg) => {
    if (msg.data?.config?.mobile_volume_steps) {
      config.value.volume.mobile_volume_steps = msg.data.config.mobile_volume_steps;
    }
  }
};

onMounted(async () => {
  await i18n.initializeLanguage();
  await loadAllConfigs();
  
  // Enregistrer tous les listeners WebSocket
  Object.entries(wsListeners).forEach(([eventType, handler]) => {
    on('settings', eventType, handler);
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

.settings-separator {
  height: 1px;
  background: var(--color-background-strong);
  margin: var(--space-02) 0;
}

/* Interface */
.dock-settings {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.app-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.app-group-title {
  color: var(--color-text-secondary);
  font-weight: 500;
}

.app-toggles {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  padding-left: var(--space-03);
}

.volume-controls-settings {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.controls-description {
  color: var(--color-text-secondary);
  font-size: var(--font-size-small);
  margin-top: var(--space-01);
}

.volume-steps-control {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.volume-steps-control label {
  color: var(--color-text-secondary);
}

.steps-control {
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

.steps-control > :first-child {
  flex: 1;
}

.steps-value {
  color: var(--color-text);
  min-width: 40px;
  text-align: right;
}

/* Volume */
.volume-settings {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.volume-limits {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
  padding-left: var(--space-04);
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

.limit-control > :first-child {
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

/* Startup volume */
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

.startup-control > :first-child {
  flex: 1;
}

.startup-value {
  color: var(--color-text);
  min-width: 40px;
  text-align: right;
}

/* Screen */
.screen-settings {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.timeout-control {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  padding-left: var(--space-04);
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

/* Spotify */
.disconnect-timer {
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

.timer-control > :first-child,
.brightness-control > :first-child {
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

/* Language */
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

/* Info */
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

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .settings-view {
    padding: var(--space-04);
  }
  
  .language-button,
  .mode-button {
    padding: var(--space-05) var(--space-04);
  }
  
  .volume-limits {
    gap: var(--space-05);
  }
  
  .limit-control,
  .timer-control,
  .brightness-control,
  .steps-control {
    gap: var(--space-04);
  }
  
  .app-toggles {
    gap: var(--space-03);
  }
}

.ios-app .settings-view {
  padding-top: var(--space-08);
}
</style>