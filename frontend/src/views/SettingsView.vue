<!-- frontend/src/views/SettingsView.vue -->
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

      <!-- Informations version (optionnel pour le futur) -->
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
import { computed } from 'vue';
import { useRouter } from 'vue-router';
import { useI18n } from '@/services/i18n';
import IconButton from '@/components/ui/IconButton.vue';

const router = useRouter();
const { setLanguage, getAvailableLanguages, getCurrentLanguage } = useI18n();

// Langues disponibles
const availableLanguages = computed(() => getAvailableLanguages());
const currentLanguage = computed(() => getCurrentLanguage());

// Actions
async function changeLanguage(languageCode) {
  await setLanguage(languageCode);
}

function goBack() {
  router.push('/');
}
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

/* Sélecteur de langue */
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

/* Grille d'informations */
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
  
  .language-selector {
    gap: var(--space-03);
  }
  
  .language-button {
    padding: var(--space-05) var(--space-04);
  }
}

.ios-app .settings-view {
  padding-top: var(--space-08);
}
</style>