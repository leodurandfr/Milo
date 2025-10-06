<!-- frontend/src/components/settings/SettingsModal.vue -->
<template>
  <div class="settings-modal">
    <!-- Vue Home : Liste des catégories -->
    <div v-if="currentView === 'home'" class="view-home">
      <ModalHeader :title="t('Configuration de Milō')" />

      <div class="settings-nav-grid">
        <SettingsCategory icon="languages" :title="t('Langues')" @click="goToView('languages')" />
        <SettingsCategory icon="applications" :title="t('Applications')" @click="goToView('apps')" />
        <SettingsCategory icon="volume" :title="t('Volume')" @click="goToView('volume')" />
        <SettingsCategory icon="display" :title="t('Écran')" @click="goToView('screen')" />
        <SettingsCategory icon="spotify" title="Spotify" @click="goToView('spotify')" />
        <SettingsCategory icon="multiroom" title="Multiroom" @click="goToView('multiroom')" />
        <SettingsCategory icon="dependencies" :title="t('Dépendances')" @click="goToView('dependencies')" />
        <SettingsCategory icon="information" :title="t('Informations')" @click="goToView('info')" />
      </div>
    </div>

    <!-- Vue Langues -->
    <div v-else-if="currentView === 'languages'" class="view-detail">
      <ModalHeader :title="t('Langues')" show-back @back="goToHome" />
      <LanguageSettings />
    </div>

    <!-- Vue Applications -->
    <div v-else-if="currentView === 'apps'" class="view-detail">
      <ModalHeader :title="t('Applications')" show-back @back="goToHome" />
      <ApplicationsSettings />
    </div>

    <!-- Vue Volume -->
    <div v-else-if="currentView === 'volume'" class="view-detail">
      <ModalHeader :title="t('Volume')" show-back @back="goToHome" />
      <VolumeSettings />
    </div>

    <!-- Vue Écran -->
    <div v-else-if="currentView === 'screen'" class="view-detail">
      <ModalHeader :title="t('Écran')" show-back @back="goToHome" />
      <ScreenSettings />
    </div>

    <!-- Vue Spotify -->
    <div v-else-if="currentView === 'spotify'" class="view-detail">
      <ModalHeader title="Spotify" show-back @back="goToHome" />
      <SpotifySettings />
    </div>

    <!-- Vue Multiroom -->
    <div v-else-if="currentView === 'multiroom'" class="view-detail">
      <ModalHeader title="Multiroom" show-back @back="goToHome" />
      <MultiroomSettings />
    </div>

    <!-- Vue Dépendances -->
    <div v-else-if="currentView === 'dependencies'" class="view-detail">
      <ModalHeader :title="t('Dépendances')" show-back @back="goToHome" />
      <UpdateManager />
    </div>

    <!-- Vue Informations -->
    <div v-else-if="currentView === 'info'" class="view-detail">
      <ModalHeader :title="t('Informations')" show-back @back="goToHome" />
      <InfoSettings />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { i18n } from '@/services/i18n';
import { useSettingsStore } from '@/stores/settingsStore';
import ModalHeader from '@/components/ui/ModalHeader.vue';
import SettingsCategory from '@/components/settings/SettingsCategory.vue';
import LanguageSettings from '@/components/settings/categories/LanguageSettings.vue';
import ApplicationsSettings from '@/components/settings/categories/ApplicationsSettings.vue';
import VolumeSettings from '@/components/settings/categories/VolumeSettings.vue';
import ScreenSettings from '@/components/settings/categories/ScreenSettings.vue';
import SpotifySettings from '@/components/settings/categories/SpotifySettings.vue';
import MultiroomSettings from '@/components/settings/categories/MultiroomSettings.vue';
import UpdateManager from '@/components/settings/categories/UpdateManager.vue';
import InfoSettings from '@/components/settings/categories/InfoSettings.vue';

const emit = defineEmits(['close']);

const { t } = useI18n();
const settingsStore = useSettingsStore();

// Navigation
const currentView = ref('home');

function goToView(view) {
  currentView.value = view;
}

function goToHome() {
  currentView.value = 'home';
}

onMounted(async () => {
  // Pré-chargement de tous les settings en parallèle
  await Promise.all([
    i18n.initializeLanguage(),
    settingsStore.loadAllSettings()
  ]);
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

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .settings-nav-grid {
    grid-template-columns: 1fr;
  }
}
</style>
