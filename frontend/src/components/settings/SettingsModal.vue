<!-- frontend/src/components/settings/SettingsModal.vue -->
<template>
  <div class="settings-modal">
    <!-- Home view: list of categories -->
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

        <IconButton v-if="settingsStore.dockApps.librespot" :title="t('audioSources.spotify')" :clickable="true" :show-caret="true" @click="goToView('spotify')">
          <template #icon>
            <img :src="spotifyIcon" alt="Spotify" />
          </template>
        </IconButton>

        <IconButton v-if="settingsStore.dockApps.multiroom" :title="t('multiroom.title')" :clickable="true" :show-caret="true" @click="goToView('multiroom')">
          <template #icon>
            <img :src="multiroomIcon" alt="Multiroom" />
          </template>
        </IconButton>

        <IconButton v-if="settingsStore.dockApps.radio" :title="t('audioSources.radio')" :clickable="true" :show-caret="true" @click="goToView('radio')">
          <template #icon>
            <img :src="radioIcon" alt="Radio" />
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

        <!-- Placeholder for an odd number of IconButtons on desktop -->
        <div v-if="shouldShowPlaceholder" class="icon-button-placeholder"></div>
      </div>
    </div>

    <!-- Languages view -->
    <div v-else-if="currentView === 'languages'" class="view-detail">
      <ModalHeader :title="t('settings.languages')" show-back @back="goToHome" />
      <LanguageSettings />
    </div>

    <!-- Applications view -->
    <div v-else-if="currentView === 'apps'" class="view-detail">
      <ModalHeader :title="t('settings.applications')" show-back @back="goToHome" />
      <ApplicationsSettings />
    </div>

    <!-- Volume view -->
    <div v-else-if="currentView === 'volume'" class="view-detail">
      <ModalHeader :title="t('settings.volume')" show-back @back="goToHome" />
      <VolumeSettings />
    </div>

    <!-- Screen view -->
    <div v-else-if="currentView === 'screen'" class="view-detail">
      <ModalHeader :title="t('settings.screen')" show-back @back="goToHome" />
      <ScreenSettings />
    </div>

    <!-- Spotify view -->
    <div v-else-if="currentView === 'spotify'" class="view-detail">
      <ModalHeader :title="t('spotifySettings.title')" show-back @back="goToHome" />
      <SpotifySettings />
    </div>

    <!-- Multiroom view -->
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

    <!-- Radio view -->
    <div v-else-if="currentView === 'radio'" class="view-detail">
      <ModalHeader title="Radio" show-back @back="goToHome" />
      <RadioSettings ref="radioSettingsRef" @go-to-add-station="goToView('radio-add')" @edit-station="handleEditStation" />
    </div>

    <!-- Radio view - Add a station -->
    <div v-else-if="currentView === 'radio-add'" class="view-detail">
      <ModalHeader title="Ajouter une station" show-back @back="handleBackFromRadioModal" />
      <AddRadioStation @back="handleBackFromRadioModal" @success="handleRadioStationAdded" />
    </div>

    <!-- Radio view - Edit a station -->
    <div v-else-if="currentView === 'radio-edit'" class="view-detail">
      <ModalHeader :title="`Modifier ${stationToEdit?.name || 'la station'}`" show-back @back="handleBackFromRadioModal" />
      <EditStation :preselected-station="stationToEdit" :can-restore="canRestoreStation" @back="handleBackFromRadioModal" @success="handleRadioStationEdited" @restore="handleRestoreStation" />
    </div>

    <!-- Updates view -->
    <div v-else-if="currentView === 'updates'" class="view-detail">
      <ModalHeader :title="t('settings.updates')" show-back @back="goToHome" />
      <UpdateManager />
    </div>

    <!-- Information view -->
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
import { useRadioStore } from '@/stores/radioStore';
import useWebSocket from '@/services/websocket';
import axios from 'axios';
import ModalHeader from '@/components/ui/ModalHeader.vue';
import Toggle from '@/components/ui/Toggle.vue';
import IconButton from '@/components/ui/IconButton.vue';
import LanguageSettings from '@/components/settings/categories/LanguageSettings.vue';

// Import settings icons
import languagesIcon from '@/assets/settings-icons/languages.svg';
import applicationsIcon from '@/assets/settings-icons/applications.svg';
import volumeIcon from '@/assets/settings-icons/volume.svg';
import displayIcon from '@/assets/settings-icons/display.svg';
import spotifyIcon from '@/assets/settings-icons/spotify.svg';
import multiroomIcon from '@/assets/settings-icons/multiroom.svg';
import updatesIcon from '@/assets/settings-icons/updates.svg';
import informationIcon from '@/assets/settings-icons/information.svg';
import radioIcon from '@/assets/settings-icons/radio.svg';
import ApplicationsSettings from '@/components/settings/categories/ApplicationsSettings.vue';
import VolumeSettings from '@/components/settings/categories/VolumeSettings.vue';
import ScreenSettings from '@/components/settings/categories/ScreenSettings.vue';
import SpotifySettings from '@/components/settings/categories/SpotifySettings.vue';
import MultiroomSettings from '@/components/settings/categories/MultiroomSettings.vue';
import RadioSettings from '@/components/settings/categories/radio/RadioSettings.vue';
import AddRadioStation from '@/components/settings/categories/radio/AddRadioStation.vue';
import EditStation from '@/components/settings/categories/radio/EditStation.vue';
import UpdateManager from '@/components/settings/categories/UpdateManager.vue';
import InfoSettings from '@/components/settings/categories/InfoSettings.vue';

const emit = defineEmits(['close']);

const { t } = useI18n();
const { on } = useWebSocket();
const settingsStore = useSettingsStore();
const unifiedStore = useUnifiedAudioStore();
const radioStore = useRadioStore();

// Navigation
const currentView = ref('home');
const radioSettingsRef = ref(null);
const stationToEdit = ref(null);

// Check if the station can be restored (only modified stations)
const canRestoreStation = computed(() => {
  return stationToEdit.value?._canRestore === true;
});

function goToView(view) {
  currentView.value = view;
}

function goToHome() {
  currentView.value = 'home';
}

// Radio navigation handling
function handleBackFromRadioModal() {
  currentView.value = 'radio';
  stationToEdit.value = null; // Reset station to edit
}

function handleEditStation(station) {
  stationToEdit.value = station;
  currentView.value = 'radio-edit';
}

async function handleRestoreStation() {
  if (!stationToEdit.value) return;

  try {
    // Call API to restore favorite metadata
    const formData = new FormData();
    formData.append('station_id', stationToEdit.value.id);

    const response = await axios.post('/api/radio/favorites/restore-metadata', formData);

    if (response.data.success) {
      // Wait a bit for backend to save
      await new Promise(resolve => setTimeout(resolve, 200));

      // Reload favorites in radioStore to update RadioSource
      await radioStore.loadStations(true);

      // Return to radio settings
      currentView.value = 'radio';
      stationToEdit.value = null;

      // After the view changes, reload the data
      await new Promise(resolve => setTimeout(resolve, 100));
      if (radioSettingsRef.value) {
        await radioSettingsRef.value.loadCustomStations();
      }
    } else {
      console.error('❌ Échec restauration station');
    }
  } catch (error) {
    console.error('❌ Erreur restauration:', error);
  }
}

function handleRadioStationAdded(station) {
  console.log('✅ Station ajoutée:', station);
  // Reload RadioSettings data
  if (radioSettingsRef.value) {
    radioSettingsRef.value.loadCustomStations();
  }
  currentView.value = 'radio';
}

async function handleRadioStationEdited(station) {
  console.log('✅ Station éditée:', station);

  // Reload favorites in radioStore to update RadioSource
  await radioStore.loadStations(true);

  // Reload RadioSettings data for the settings view
  if (radioSettingsRef.value) {
    radioSettingsRef.value.loadCustomStations();
  }

  stationToEdit.value = null; // Reset station to edit
  currentView.value = 'radio';
}

// Placeholder for odd grid
const shouldShowPlaceholder = computed(() => {
  // Count the number of visible IconButtons
  let count = 6; // Base: Languages, Applications, Volume, Screen, Updates, Information
  if (settingsStore.dockApps.librespot) count++;
  if (settingsStore.dockApps.multiroom) count++;
  if (settingsStore.dockApps.radio) count++;

  // Return true if odd
  return count % 2 !== 0;
});

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
    // Reset toggling when the state change is complete
    isMultiroomToggling.value = false;
  }
}, 100);

onMounted(async () => {
  // Preload all settings in parallel
  await Promise.all([
    i18n.initializeLanguage(),
    settingsStore.loadAllSettings()
  ]);

  // Note: Snapcast settings are now loaded directly by MultiroomSettings.vue via snapcastStore

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

/* Placeholder for odd grid */
.icon-button-placeholder {
  background: var(--color-background-neutral-50);
  border-radius: var(--radius-05);
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .settings-nav-grid {
    grid-template-columns: 1fr;
  }

  /* Hide placeholder on mobile */
  .icon-button-placeholder {
    display: none;
  }
}
</style>