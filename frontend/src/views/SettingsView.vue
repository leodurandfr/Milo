<!-- frontend/src/views/SettingsView.vue - Version avec limites de volume -->
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
          <!-- Limites de volume -->
          <div class="volume-limits">
            <div class="limit-group">
              <label class="text-mono">{{ $t('Volume minimum') }}</label>
              <div class="limit-control">
                <RangeSlider 
                  v-model="volumeLimits.alsa_min" 
                  :min="0" 
                  :max="maxMinVolume" 
                  :step="1"
                  @input="handleMinVolumeChange"
                  class="limit-slider" 
                />
                <span class="limit-value text-mono">{{ volumeLimits.alsa_min }}</span>
              </div>
            </div>

            <div class="limit-group">
              <label class="text-mono">{{ $t('Volume maximum') }}</label>
              <div class="limit-control">
                <RangeSlider 
                  v-model="volumeLimits.alsa_max" 
                  :min="minMaxVolume" 
                  :max="100" 
                  :step="1"
                  @input="handleMaxVolumeChange"
                  class="limit-slider" 
                />
                <span class="limit-value text-mono">{{ volumeLimits.alsa_max }}</span>
              </div>
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
                    left: `${volumeLimits.alsa_min}%`, 
                    width: `${volumeLimits.alsa_max - volumeLimits.alsa_min}%` 
                  }"
                ></div>
              </div>
              <div class="range-labels text-mono">
                <span>{{ volumeLimits.alsa_min }}%</span>
                <span>{{ volumeLimits.alsa_max }}%</span>
              </div>
            </div>
          </div>

          <!-- Messages de validation -->
          <div v-if="volumeValidation.error" class="validation-error">
            {{ volumeValidation.message }}
          </div>
          
          <!-- Bouton d'application -->
          <Button 
            variant="primary" 
            :disabled="!volumeValidation.canApply || applyingVolume" 
            @click="applyVolumeLimits"
          >
            {{ applyingVolume ? $t('Application en cours...') : $t('Appliquer les limites') }}
          </Button>
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
import { ref, computed, onMounted, watch } from 'vue';
import { useRouter } from 'vue-router';
import { useI18n } from '@/services/i18n';
import { i18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import IconButton from '@/components/ui/IconButton.vue';
import Button from '@/components/ui/Button.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import axios from 'axios';

const router = useRouter();
const { setLanguage, getAvailableLanguages, getCurrentLanguage } = useI18n();
const { on } = useWebSocket();

// État des limites de volume
const volumeLimits = ref({
  alsa_min: 0,
  alsa_max: 65
});

const originalVolumeLimits = ref({
  alsa_min: 0,
  alsa_max: 65
});

const applyingVolume = ref(false);

// Langues disponibles
const availableLanguages = computed(() => getAvailableLanguages());
const currentLanguage = computed(() => getCurrentLanguage());

// Validation des limites de volume
const maxMinVolume = computed(() => Math.max(0, volumeLimits.value.alsa_max - 10));
const minMaxVolume = computed(() => Math.min(100, volumeLimits.value.alsa_min + 10));

const volumeValidation = computed(() => {
  const min = volumeLimits.value.alsa_min;
  const max = volumeLimits.value.alsa_max;
  const range = max - min;
  
  if (range < 10) {
    return {
      error: true,
      message: 'La plage de volume doit être d\'au moins 10',
      canApply: false
    };
  }
  
  if (min < 0 || max > 100) {
    return {
      error: true,
      message: 'Les limites doivent être entre 0 et 100',
      canApply: false
    };
  }
  
  // Vérifier s'il y a des changements
  const hasChanges = min !== originalVolumeLimits.value.alsa_min || 
                    max !== originalVolumeLimits.value.alsa_max;
  
  return {
    error: false,
    message: '',
    canApply: hasChanges
  };
});

// Gestionnaires de changement avec validation automatique
function handleMinVolumeChange(newMin) {
  if (newMin + 10 > volumeLimits.value.alsa_max) {
    volumeLimits.value.alsa_max = Math.min(100, newMin + 10);
  }
}

function handleMaxVolumeChange(newMax) {
  if (newMax - 10 < volumeLimits.value.alsa_min) {
    volumeLimits.value.alsa_min = Math.max(0, newMax - 10);
  }
}

// Actions
async function loadVolumeLimits() {
  try {
    const response = await axios.get('/api/settings/volume-limits');
    if (response.data.status === 'success') {
      const limits = response.data.limits;
      volumeLimits.value = {
        alsa_min: limits.alsa_min,
        alsa_max: limits.alsa_max
      };
      originalVolumeLimits.value = { ...volumeLimits.value };
    }
  } catch (error) {
    console.error('Error loading volume limits:', error);
  }
}

async function applyVolumeLimits() {
  if (!volumeValidation.value.canApply || applyingVolume.value) return;
  
  applyingVolume.value = true;
  
  try {
    const response = await axios.post('/api/settings/volume-limits', {
      alsa_min: volumeLimits.value.alsa_min,
      alsa_max: volumeLimits.value.alsa_max
    });
    
    if (response.data.status === 'success') {
      originalVolumeLimits.value = { ...volumeLimits.value };
      console.log('Volume limits applied successfully');
    } else {
      console.error('Failed to apply volume limits:', response.data.message);
    }
    
  } catch (error) {
    console.error('Error applying volume limits:', error);
  } finally {
    applyingVolume.value = false;
  }
}

async function changeLanguage(languageCode) {
  await setLanguage(languageCode);
}

function goBack() {
  router.push('/');
}

// Initialisation et WebSocket
onMounted(async () => {
  await i18n.initializeLanguage();
  await loadVolumeLimits();
  
  // Écouter les changements de langue via WebSocket
  on('settings', 'language_changed', (message) => {
    if (message.data?.language) {
      i18n.handleLanguageChanged(message.data.language);
    }
  });
  
  // Écouter les changements de limites de volume via WebSocket
  on('settings', 'volume_limits_changed', (message) => {
    if (message.data?.limits) {
      const newLimits = message.data.limits;
      volumeLimits.value = {
        alsa_min: newLimits.alsa_min,
        alsa_max: newLimits.alsa_max
      };
      originalVolumeLimits.value = { ...volumeLimits.value };
      console.log('Volume limits updated via WebSocket:', newLimits);
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

/* === SECTION VOLUME === */

.volume-settings {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
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
  
  .limit-control {
    gap: var(--space-04);
  }
}

.ios-app .settings-view {
  padding-top: var(--space-08);
}
</style>