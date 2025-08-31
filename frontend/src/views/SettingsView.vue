<!-- frontend/src/views/SettingsView.vue - Version OPTIM avec handler unifié -->
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
      <!-- Volume -->
      <section class="settings-section">
        <h2 class="heading-2">{{ $t('Volume') }}</h2>
        
        <div class="volume-settings">
          <!-- Toggle limites -->
          <div class="volume-limits-toggle">
            <Toggle 
              v-model="config.volume.limits_enabled" 
              :title="$t('Limites de volume')"
              @change="updateSetting('volume-limits/toggle', { enabled: $event })"
            />
            <div class="toggle-description text-mono">
              {{ $t('Restreindre la plage de volume utilisable') }}
            </div>
          </div>

          <!-- Limites volume -->
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

          <!-- Volume démarrage -->
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
          <!-- Timeout -->
          <div class="timeout-control">
            <label class="text-mono">{{ $t('Mise en veille automatique') }}</label>
            <div class="timer-control">
              <RangeSlider 
                v-model="config.screen.screen_timeout_seconds" 
                :min="3" 
                :max="3600" 
                :step="1"
                @input="debouncedUpdate('screen-timeout', { screen_timeout_seconds: $event })"
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
    startup_volume: 37
  },
  spotify: {
    auto_disconnect_delay: 10.0
  },
  screen: {
    screen_timeout_seconds: 10,
    brightness_on: 5
  }
});

const availableLanguages = computed(() => getAvailableLanguages());
const currentLanguage = computed(() => getCurrentLanguage());

// Formatage durée
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

// Handlers spécialisés
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

// Chargement initial
async function loadAllConfigs() {
  try {
    const [volumeLimits, volumeStartup, spotify, screenTimeout, brightness] = await Promise.all([
      axios.get('/api/settings/volume-limits'),
      axios.get('/api/settings/volume-startup'),
      axios.get('/api/settings/spotify-disconnect'),
      axios.get('/api/settings/screen-timeout'),
      axios.get('/api/settings/screen-brightness')
    ]);
    
    if (volumeLimits.data.status === 'success') {
      Object.assign(config.value.volume, volumeLimits.data.limits);
    }
    
    if (volumeStartup.data.status === 'success') {
      Object.assign(config.value.volume, volumeStartup.data.config);
    }
    
    if (spotify.data.status === 'success') {
      Object.assign(config.value.spotify, spotify.data.config);
    }
    
    if (screenTimeout.data.status === 'success') {
      Object.assign(config.value.screen, screenTimeout.data.config);
    }
    
    if (brightness.data.status === 'success') {
      Object.assign(config.value.screen, brightness.data.config);
    }
    
  } catch (error) {
    console.error('Error loading configs:', error);
  }
}

// WebSocket listeners unifiés
const wsListeners = {
  'language_changed': (msg) => i18n.handleLanguageChanged(msg.data?.language),
  'volume_limits_changed': (msg) => Object.assign(config.value.volume, msg.data?.limits || {}),
  'volume_limits_toggled': (msg) => config.value.volume.limits_enabled = msg.data?.limits_enabled,
  'volume_startup_changed': (msg) => Object.assign(config.value.volume, msg.data?.config || {}),
  'spotify_disconnect_changed': (msg) => Object.assign(config.value.spotify, msg.data?.config || {}),
  'screen_timeout_changed': (msg) => Object.assign(config.value.screen, msg.data?.config || {}),
  'screen_brightness_changed': (msg) => Object.assign(config.value.screen, msg.data?.config || {})
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

/* Volume */
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

/* Spotify & Screen */
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
  .brightness-control {
    gap: var(--space-04);
  }
}

.ios-app .settings-view {
  padding-top: var(--space-08);
}
</style>