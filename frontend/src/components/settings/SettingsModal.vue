<!-- frontend/src/components/settings/SettingsModal.vue -->
<template>
  <div class="settings-modal">
    <!-- Single ModalHeader outside transition -->
    <ModalHeader
      :title="headerTitle"
      :show-back="canGoBack"
      :actions-key="currentView"
      @back="handleBack"
    >
      <template v-if="currentView === 'multiroom'" #actions="{ iconType }">
        <Toggle
          v-model="isMultiroomActive"
          :type="iconType"
          :disabled="unifiedStore.systemState.transitioning || isMultiroomToggling"
          @change="handleMultiroomToggle"
        />
      </template>
    </ModalHeader>

    <!-- Content area -->
    <Transition name="view-fade" mode="out-in">
        <!-- Home view: list of categories -->
        <div v-if="currentView === 'home'" key="home" class="view-content">
          <div class="settings-nav-grid">
            <ListItemButton :title="t('settings.languages')" :show-caret="true" @click="goToView('languages')">
              <template #icon>
                <img :src="languagesIcon" alt="Languages" />
              </template>
            </ListItemButton>

            <ListItemButton :title="t('settings.applications')" :show-caret="true" @click="goToView('apps')">
              <template #icon>
                <img :src="applicationsIcon" alt="Applications" />
              </template>
            </ListItemButton>

            <ListItemButton :title="t('settings.volume')" :show-caret="true" @click="goToView('volume')">
              <template #icon>
                <img :src="volumeIcon" alt="Volume" />
              </template>
            </ListItemButton>

            <ListItemButton :title="t('settings.screen')" :show-caret="true" @click="goToView('screen')">
              <template #icon>
                <img :src="displayIcon" alt="Display" />
              </template>
            </ListItemButton>

            <ListItemButton v-if="settingsStore.dockApps.librespot" :title="t('audioSources.spotify')" :show-caret="true" @click="goToView('spotify')">
              <template #icon>
                <img :src="spotifyIcon" alt="Spotify" />
              </template>
            </ListItemButton>

            <ListItemButton v-if="settingsStore.dockApps.multiroom" :title="t('multiroom.title')" :show-caret="true" @click="goToView('multiroom')">
              <template #icon>
                <img :src="multiroomIcon" alt="Multiroom" />
              </template>
            </ListItemButton>

            <ListItemButton v-if="settingsStore.dockApps.radio" :title="t('audioSources.radio')" :show-caret="true" @click="goToView('radio')">
              <template #icon>
                <img :src="radioIcon" alt="Radio" />
              </template>
            </ListItemButton>

            <ListItemButton v-if="settingsStore.dockApps.podcast" :title="t('audioSources.podcasts')" :show-caret="true" @click="goToView('podcast')">
              <template #icon>
                <img :src="podcastIcon" alt="Podcasts" />
              </template>
            </ListItemButton>

            <ListItemButton :title="t('settings.updates')" :show-caret="true" @click="goToView('updates')">
              <template #icon>
                <img :src="updatesIcon" alt="Updates" />
              </template>
            </ListItemButton>

            <ListItemButton :title="t('settings.information')" :show-caret="true" @click="goToView('info')">
              <template #icon>
                <img :src="informationIcon" alt="Information" />
              </template>
            </ListItemButton>

            <!-- Placeholder for an odd number of IconButtons on desktop -->
            <div v-if="shouldShowPlaceholder" class="icon-button-placeholder"></div>
          </div>
        </div>

        <!-- Languages view -->
        <LanguageSettings v-else-if="currentView === 'languages'" key="languages" class="view-content" />

        <!-- Applications view -->
        <ApplicationsSettings v-else-if="currentView === 'apps'" key="apps" class="view-content" />

        <!-- Volume view -->
        <VolumeSettings v-else-if="currentView === 'volume'" key="volume" class="view-content" />

        <!-- Screen view -->
        <ScreenSettings v-else-if="currentView === 'screen'" key="screen" class="view-content" />

        <!-- Spotify view -->
        <SpotifySettings v-else-if="currentView === 'spotify'" key="spotify" class="view-content" />

        <!-- Multiroom view -->
        <MultiroomSettings v-else-if="currentView === 'multiroom'" key="multiroom" class="view-content" />

        <!-- Radio view -->
        <RadioSettings v-else-if="currentView === 'radio'" key="radio" ref="radioSettingsRef" class="view-content" @go-to-add-station="goToView('radio-add')" @edit-station="handleEditStation" />

        <!-- Radio view - Add a station -->
        <ManageStation v-else-if="currentView === 'radio-add'" key="radio-add" class="view-content" mode="add" @back="handleBackFromRadioModal" @success="handleRadioStationAdded" />

        <!-- Radio view - Edit a station -->
        <ManageStation v-else-if="currentView === 'radio-edit'" key="radio-edit" class="view-content" mode="edit" :station="stationToEdit" :can-restore="canRestoreStation" :can-delete="canDeleteStation" @back="handleBackFromRadioModal" @success="handleRadioStationEdited" @restore="handleRestoreStation" @delete="handleDeleteStation" />

        <!-- Podcast view -->
        <PodcastSettings v-else-if="currentView === 'podcast'" key="podcast" class="view-content" />

        <!-- Updates view -->
        <UpdateManager v-else-if="currentView === 'updates'" key="updates" class="view-content" />

        <!-- Information view -->
        <InfoSettings v-else-if="currentView === 'info'" key="info" class="view-content" />
    </Transition>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, inject, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { i18n } from '@/services/i18n';
import { useSettingsStore } from '@/stores/settingsStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useRadioStore } from '@/stores/radioStore';
import { useNavigationStack } from '@/composables/useNavigationStack';
import useWebSocket from '@/services/websocket';
import axios from 'axios';
import ModalHeader from '@/components/ui/ModalHeader.vue';
import Toggle from '@/components/ui/Toggle.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
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
import podcastIcon from '@/assets/settings-icons/podcast.svg';
import ApplicationsSettings from '@/components/settings/categories/ApplicationsSettings.vue';
import VolumeSettings from '@/components/settings/categories/VolumeSettings.vue';
import ScreenSettings from '@/components/settings/categories/ScreenSettings.vue';
import SpotifySettings from '@/components/settings/categories/SpotifySettings.vue';
import MultiroomSettings from '@/components/settings/categories/MultiroomSettings.vue';
import RadioSettings from '@/components/settings/categories/radio/RadioSettings.vue';
import ManageStation from '@/components/settings/categories/radio/ManageStation.vue';
import PodcastSettings from '@/components/settings/categories/PodcastSettings.vue';
import UpdateManager from '@/components/settings/categories/UpdateManager.vue';
import InfoSettings from '@/components/settings/categories/InfoSettings.vue';

const props = defineProps({
  initialView: {
    type: String,
    default: 'home'
  }
});

const emit = defineEmits(['close']);

const { t } = useI18n();
const { on } = useWebSocket();
const settingsStore = useSettingsStore();
const unifiedStore = useUnifiedAudioStore();
const radioStore = useRadioStore();

// Inject modal scroll reset function
const resetScroll = inject('modalResetScroll', () => {});

// Navigation with stack
const { currentView, canGoBack, push, back, reset, goTo } = useNavigationStack('home');
const radioSettingsRef = ref(null);
const stationToEdit = ref(null);

// Dynamic header title based on current view
const headerTitle = computed(() => {
  const titles = {
    'home': t('settings.title'),
    'languages': t('settings.languages'),
    'apps': t('settings.applications'),
    'volume': t('settings.volume'),
    'screen': t('settings.screen'),
    'spotify': t('spotifySettings.title'),
    'multiroom': t('multiroom.title'),
    'radio': 'Radio',
    'radio-add': t('radio.manageStation.addStationTitle'),
    'radio-edit': t('radio.manageStation.editStationTitle'),
    'podcast': t('podcastSettings.title'),
    'updates': t('settings.updates'),
    'info': t('settings.information')
  };
  return titles[currentView.value] || t('settings.title');
});

// Watch initialView prop for direct navigation (e.g., from CredentialsRequired)
watch(() => props.initialView, (newView) => {
  if (newView && newView !== 'home') {
    goTo(newView);
  } else {
    reset();
  }
}, { immediate: true });

// Reset scroll position when navigating between views
watch(currentView, () => {
  resetScroll();
});

// Check if the station can be restored (only modified stations)
const canRestoreStation = computed(() => {
  return stationToEdit.value?._canRestore === true;
});

// Check if the station can be deleted (only manually added stations)
const canDeleteStation = computed(() => {
  return stationToEdit.value?._canDelete === true;
});

function goToView(view) {
  push(view);
}

function goToHome() {
  reset();
}

function handleBack() {
  back();
}

// Radio navigation handling
function handleBackFromRadioModal() {
  back();
  stationToEdit.value = null; // Reset station to edit
}

function handleEditStation(station) {
  stationToEdit.value = station;
  push('radio-edit');
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
      back();
      stationToEdit.value = null;

      // After the view changes, reload the data
      await new Promise(resolve => setTimeout(resolve, 100));
      if (radioSettingsRef.value) {
        await radioSettingsRef.value.loadCustomStations();
      }
    } else {
      console.error('Failed to restore station');
    }
  } catch (error) {
    console.error('Error restoring station:', error);
  }
}

async function handleDeleteStation() {
  if (!stationToEdit.value) return;

  try {
    const success = await radioStore.removeCustomStation(stationToEdit.value.id);

    if (success) {
      console.log('Station deleted');

      // Wait a bit for backend to save
      await new Promise(resolve => setTimeout(resolve, 200));

      // Reload favorites in radioStore to update RadioSource
      await radioStore.loadStations(true);

      // Return to radio settings
      back();
      stationToEdit.value = null;

      // After the view changes, reload the data
      await new Promise(resolve => setTimeout(resolve, 100));
      if (radioSettingsRef.value) {
        await radioSettingsRef.value.loadCustomStations();
      }
    } else {
      console.error('Failed to delete station');
    }
  } catch (error) {
    console.error('Error deleting station:', error);
  }
}

function handleRadioStationAdded(station) {
  console.log('Station added:', station);
  // Reload RadioSettings data
  if (radioSettingsRef.value) {
    radioSettingsRef.value.loadCustomStations();
  }
  back();
}

async function handleRadioStationEdited(station) {
  console.log('Station edited:', station);

  // Reload favorites in radioStore to update RadioSource
  await radioStore.loadStations(true);

  // Reload RadioSettings data for the settings view
  if (radioSettingsRef.value) {
    radioSettingsRef.value.loadCustomStations();
  }

  stationToEdit.value = null; // Reset station to edit
  back();
}

// Placeholder for odd grid
const shouldShowPlaceholder = computed(() => {
  // Count the number of visible IconButtons
  let count = 6; // Base: Languages, Applications, Volume, Screen, Updates, Information
  if (settingsStore.dockApps.librespot) count++;
  if (settingsStore.dockApps.multiroom) count++;
  if (settingsStore.dockApps.radio) count++;
  if (settingsStore.dockApps.podcast) count++;

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

.view-content {
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

/* View transition - using design system variables */
.view-fade-enter-active,
.view-fade-leave-active {
  transition: opacity var(--transition-fast);
}

.view-fade-enter-from,
.view-fade-leave-to {
  opacity: 0;
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
