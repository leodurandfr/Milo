<!-- frontend/src/components/settings/SettingsModal.vue -->
<template>
  <div class="settings-modal">
    <!-- Vue Home : Liste des catégories -->
    <div v-if="currentView === 'home'" class="view-home">
      <ModalHeader :title="t('settings.title')" />

      <div class="settings-nav-grid">
        <IconButton :title="t('settings.languages')" :clickable="true" :show-caret="true" @click="goToView('languages')">
          <template #icon>
            <img :src="languagesIcon" alt="Languages" />
          </template>
        </IconButton>

        <IconButton :title="t('settings.applications')" :clickable="true" :show-caret="true" @click="goToView('apps')">
          <template #icon>
            <img :src="applicationsIcon" alt="Applications" />
          </template>
        </IconButton>

        <IconButton :title="t('settings.volume')" :clickable="true" :show-caret="true" @click="goToView('volume')">
          <template #icon>
            <img :src="volumeIcon" alt="Volume" />
          </template>
        </IconButton>

        <IconButton :title="t('settings.screen')" :clickable="true" :show-caret="true" @click="goToView('screen')">
          <template #icon>
            <img :src="displayIcon" alt="Display" />
          </template>
        </IconButton>

        <IconButton v-if="settingsStore.dockApps.librespot" :title="t('spotifySettings.title')" :clickable="true" :show-caret="true" @click="goToView('spotify')">
          <template #icon>
            <img :src="spotifyIcon" alt="Spotify" />
          </template>
        </IconButton>

        <IconButton v-if="settingsStore.dockApps.multiroom" :title="t('multiroom.title')" :clickable="true" :show-caret="true" @click="goToView('multiroom')">
          <template #icon>
            <img :src="multiroomIcon" alt="Multiroom" />
          </template>
        </IconButton>

        <IconButton v-if="settingsStore.dockApps.radio" title="Radio" :clickable="true" :show-caret="true" @click="goToView('radio')">
          <template #icon>
            <img :src="applicationsIcon" alt="Radio" />
          </template>
        </IconButton>

        <IconButton :title="t('settings.updates')" :clickable="true" :show-caret="true" @click="goToView('updates')">
          <template #icon>
            <img :src="updatesIcon" alt="Updates" />
          </template>
        </IconButton>

        <IconButton :title="t('settings.information')" :clickable="true" :show-caret="true" @click="goToView('info')">
          <template #icon>
            <img :src="informationIcon" alt="Information" />
          </template>
        </IconButton>
      </div>
    </div>

    <!-- Vue Langues -->
    <div v-else-if="currentView === 'languages'" class="view-detail">
      <ModalHeader :title="t('settings.languages')" show-back @back="goToHome" />
      <LanguageSettings />
    </div>

    <!-- Vue Applications -->
    <div v-else-if="currentView === 'apps'" class="view-detail">
      <ModalHeader :title="t('settings.applications')" show-back @back="goToHome" />
      <ApplicationsSettings />
    </div>

    <!-- Vue Volume -->
    <div v-else-if="currentView === 'volume'" class="view-detail">
      <ModalHeader :title="t('settings.volume')" show-back @back="goToHome" />
      <VolumeSettings />
    </div>

    <!-- Vue Écran -->
    <div v-else-if="currentView === 'screen'" class="view-detail">
      <ModalHeader :title="t('settings.screen')" show-back @back="goToHome" />
      <ScreenSettings />
    </div>

    <!-- Vue Spotify -->
    <div v-else-if="currentView === 'spotify'" class="view-detail">
      <ModalHeader :title="t('spotifySettings.title')" show-back @back="goToHome" />
      <SpotifySettings />
    </div>

    <!-- Vue Multiroom -->
    <div v-else-if="currentView === 'multiroom'" class="view-detail">
      <ModalHeader :title="t('multiroom.title')" show-back @back="goToHome">
        <template #actions>
          <Toggle
            v-model="isMultiroomActive"
            variant="primary"
            :disabled="unifiedStore.systemState.transitioning || isMultiroomToggling"
            @change="handleMultiroomToggle"
          />
        </template>
      </ModalHeader>
      <MultiroomSettings />
    </div>

    <!-- Vue Radio -->
    <div v-else-if="currentView === 'radio'" class="view-detail">
      <ModalHeader title="Radio" show-back @back="goToHome" />
      <RadioSettings />
    </div>

    <!-- Vue Mises à jour -->
    <div v-else-if="currentView === 'updates'" class="view-detail">
      <ModalHeader :title="t('settings.updates')" show-back @back="goToHome" />
      <UpdateManager />
    </div>

    <!-- Vue Informations -->
    <div v-else-if="currentView === 'info'" class="view-detail">
      <ModalHeader :title="t('settings.information')" show-back @back="goToHome" />
      <InfoSettings />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { i18n } from '@/services/i18n';
import { useSettingsStore } from '@/stores/settingsStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';
import ModalHeader from '@/components/ui/ModalHeader.vue';
import Toggle from '@/components/ui/Toggle.vue';
import IconButton from '@/components/ui/IconButton.vue';
import LanguageSettings from '@/components/settings/categories/LanguageSettings.vue';

// Import des icônes settings
import languagesIcon from '@/assets/settings-icons/languages.svg';
import applicationsIcon from '@/assets/settings-icons/applications.svg';
import volumeIcon from '@/assets/settings-icons/volume.svg';
import displayIcon from '@/assets/settings-icons/display.svg';
import spotifyIcon from '@/assets/settings-icons/spotify.svg';
import multiroomIcon from '@/assets/settings-icons/multiroom.svg';
import updatesIcon from '@/assets/settings-icons/updates.svg';
import informationIcon from '@/assets/settings-icons/information.svg';
import ApplicationsSettings from '@/components/settings/categories/ApplicationsSettings.vue';
import VolumeSettings from '@/components/settings/categories/VolumeSettings.vue';
import ScreenSettings from '@/components/settings/categories/ScreenSettings.vue';
import SpotifySettings from '@/components/settings/categories/SpotifySettings.vue';
import MultiroomSettings from '@/components/settings/categories/MultiroomSettings.vue';
import RadioSettings from '@/components/settings/categories/RadioSettings.vue';
import UpdateManager from '@/components/settings/categories/UpdateManager.vue';
import InfoSettings from '@/components/settings/categories/InfoSettings.vue';

const emit = defineEmits(['close']);

const { t } = useI18n();
const { on } = useWebSocket();
const settingsStore = useSettingsStore();
const unifiedStore = useUnifiedAudioStore();

// Navigation
const currentView = ref('home');

function goToView(view) {
  currentView.value = view;
}

function goToHome() {
  currentView.value = 'home';
}

// Multiroom toggle
const isMultiroomToggling = ref(false);
const isMultiroomActive = computed(() => unifiedStore.systemState.multiroom_enabled);

async function handleMultiroomToggle(enabled) {
  await unifiedStore.setMultiroomEnabled(enabled);
}

function handleMultiroomEnabling() {
  isMultiroomToggling.value = true;
}

function handleMultiroomDisabling() {
  isMultiroomToggling.value = true;
}

// Watcher multiroom state (similar to SnapcastModal.vue)
let lastMultiroomState = isMultiroomActive.value;
const watcherInterval = setInterval(() => {
  if (lastMultiroomState !== isMultiroomActive.value) {
    lastMultiroomState = isMultiroomActive.value;
    // Réinitialiser le toggling quand le changement d'état est terminé
    isMultiroomToggling.value = false;
  }
}, 100);

onMounted(async () => {
  // Pré-chargement de tous les settings en parallèle
  await Promise.all([
    i18n.initializeLanguage(),
    settingsStore.loadAllSettings()
  ]);

  // Note: Les settings snapcast sont maintenant chargés directement par MultiroomSettings.vue via snapcastStore

  on('routing', 'multiroom_enabling', handleMultiroomEnabling);
  on('routing', 'multiroom_disabling', handleMultiroomDisabling);
});

onUnmounted(() => {
  clearInterval(watcherInterval);
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
  gap: var(--space-01);
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .settings-nav-grid {
    grid-template-columns: 1fr;
  }
}
</style>
