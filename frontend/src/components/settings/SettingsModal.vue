<!-- frontend/src/components/settings/SettingsModal.vue -->
<template>
  <div class="settings-modal">
    <!-- Single ModalHeader outside transition -->
    <ModalHeader ref="headerRef" :title="headerTitle" :show-back="canGoBack" :actions-key="currentView"
      @back="back">
      <template v-if="currentView === 'multiroom'" #actions="{ iconType }">
        <Toggle :model-value="isMultiroomActive" :type="iconType"
          :disabled="unifiedStore.systemState.transitioning || isMultiroomToggling" @change="handleMultiroomToggle" />
      </template>
    </ModalHeader>

    <!-- Content area (wrapper provides positioning context for cross-fade overlay) -->
    <div class="transition-wrapper">
    <Transition name="fade-slide" @before-leave="onBeforeLeave" @enter="onEnter" @after-leave="onAfterLeave">
      <!-- Home view: list of categories -->
      <div v-if="currentView === 'home'" key="home" class="view-content">
        <div class="settings-nav-grid">
          <ListItemButton :title="t('settings.languages')" action="caret" @click="push('languages')">
            <template #icon>
              <img :src="languagesIcon" alt="Languages" />
            </template>
          </ListItemButton>

          <ListItemButton :title="t('settings.dock')" action="caret" @click="push('apps')">
            <template #icon>
              <img :src="applicationsIcon" alt="Applications" />
            </template>
          </ListItemButton>

          <ListItemButton :title="t('settings.volume')" action="caret" @click="push('volume')">
            <template #icon>
              <img :src="volumeIcon" alt="Volume" />
            </template>
          </ListItemButton>

          <ListItemButton :title="t('settings.screen')" action="caret" @click="push('screen')">
            <template #icon>
              <img :src="displayIcon" alt="Display" />
            </template>
          </ListItemButton>
          
          <ListItemButton v-if="settingsStore.dockApps.multiroom" :title="t('multiroom.title')" action="caret"
            @click="push('multiroom')">
            <template #icon>
              <img :src="multiroomIcon" alt="Multiroom" />
            </template>
          </ListItemButton>

          <ListItemButton v-if="settingsStore.dockApps.spotify" :title="t('audioSources.spotify')" action="caret"
            @click="push('spotify')">
            <template #icon>
              <img :src="spotifyIcon" alt="Spotify" />
            </template>
          </ListItemButton>

          <ListItemButton v-if="settingsStore.dockApps.mac" :title="t('macSettings.title')" action="caret"
            @click="push('macos')">
            <template #icon>
              <img :src="macosIcon" alt="Mac" />
            </template>
          </ListItemButton>

          <ListItemButton v-if="settingsStore.dockApps.radio" :title="t('audioSources.radio')" action="caret"
            @click="push('radio')">
            <template #icon>
              <img :src="radioIcon" alt="Radio" />
            </template>
          </ListItemButton>

          <ListItemButton v-if="settingsStore.dockApps.podcast" :title="t('audioSources.podcasts')" action="caret"
            @click="push('podcast')">
            <template #icon>
              <img :src="podcastIcon" alt="Podcasts" />
            </template>
          </ListItemButton>

          <ListItemButton :title="t('settings.updates')" action="caret" @click="push('updates')">
            <template #icon>
              <img :src="updatesIcon" alt="Updates" />
            </template>
          </ListItemButton>

          <ListItemButton :title="t('settings.information')" action="caret" @click="push('info')">
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
      <DockSettings v-else-if="currentView === 'apps'" key="apps" class="view-content" />

      <!-- Volume view -->
      <VolumeSettings v-else-if="currentView === 'volume'" key="volume" class="view-content" />

      <!-- Screen view -->
      <ScreenSettings v-else-if="currentView === 'screen'" key="screen" class="view-content" />

      <!-- Spotify view -->
      <SpotifySettings v-else-if="currentView === 'spotify'" key="spotify" class="view-content" />

      <!-- Multiroom view -->
      <MultiroomSettings v-else-if="currentView === 'multiroom'" key="multiroom" class="view-content"
        @edit-zone="handleEditZone" @create-zone="handleCreateZone" @edit-client="handleEditClient" />

      <!-- Multiroom zone edit view -->
      <ZoneEdit v-else-if="currentView === 'multiroom-zone-edit'" key="multiroom-zone-edit" class="view-content"
        :group-id="zoneGroupId" :enable-client-renaming="true" @back="handleZoneSaved" @saved="handleZoneSaved" />

      <!-- Multiroom client edit view -->
      <ClientEdit v-else-if="currentView === 'multiroom-client-edit'" key="multiroom-client-edit" class="view-content"
        :mac-id="macIdToEdit" @back="handleClientSaved" @saved="handleClientSaved" />

      <!-- Radio view -->
      <RadioSettings v-else-if="currentView === 'radio'" key="radio" ref="radioSettingsRef" class="view-content"
        @go-to-add-station="goToView('radio-add')" @edit-station="handleEditStation" />

      <!-- Radio view - Add a station -->
      <ManageStation v-else-if="currentView === 'radio-add'" key="radio-add" class="view-content" mode="add"
        @back="handleBackFromRadioModal" @success="handleRadioStationAdded" />

      <!-- Radio view - Edit a station -->
      <ManageStation v-else-if="currentView === 'radio-edit'" key="radio-edit" class="view-content" mode="edit"
        :station="stationToEdit" :can-restore="canRestoreStation" :can-delete="canDeleteStation"
        @back="handleBackFromRadioModal" @success="handleRadioStationEdited" @restore="handleRestoreStation"
        @delete="handleDeleteStation" />

      <!-- Podcast view -->
      <PodcastSettings v-else-if="currentView === 'podcast'" key="podcast" class="view-content" />

      <!-- Mac streaming view -->
      <MacosSettings v-else-if="currentView === 'macos'" key="macos" class="view-content" />

      <!-- Updates view -->
      <UpdateManager v-else-if="currentView === 'updates'" key="updates" class="view-content" />

      <!-- Information view -->
      <InfoSettings v-else-if="currentView === 'info'" key="info" class="view-content" />
    </Transition>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, inject, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { i18n } from '@/services/i18n';
import { useSettingsStore } from '@/stores/settingsStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useRadioStore } from '@/stores/radioStore';
import { useNavigationStack } from '@/composables/useNavigationStack';
import useWebSocket from '@/services/websocket';
import { logger } from '@/services/logger';
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
import macosIcon from '@/assets/settings-icons/macos.svg';
import DockSettings from '@/components/settings/categories/DockSettings.vue';
import VolumeSettings from '@/components/settings/categories/VolumeSettings.vue';
import ScreenSettings from '@/components/settings/categories/ScreenSettings.vue';
import SpotifySettings from '@/components/settings/categories/SpotifySettings.vue';
import MultiroomSettings from './categories/multiroom/MultiroomSettings.vue';
import ZoneEdit from './categories/multiroom/ZoneEdit.vue';
import ClientEdit from './categories/multiroom/ClientEdit.vue';
import RadioSettings from '@/components/settings/categories/radio/RadioSettings.vue';
import ManageStation from '@/components/settings/categories/radio/ManageStation.vue';
import PodcastSettings from '@/components/settings/categories/PodcastSettings.vue';
import MacosSettings from '@/components/settings/categories/MacosSettings.vue';
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

// Inject modal scroll reset function and content ref for scroll detection
const resetScroll = inject('modalResetScroll', () => { });
const modalContentRef = inject('modalContentRef', null);

// Navigation
const { currentView, canGoBack, push, back, reset, goTo } = useNavigationStack('home');

// Refs
const headerRef = ref(null);
const radioSettingsRef = ref(null);
const stationToEdit = ref(null);
const zoneGroupId = ref(null);
const macIdToEdit = ref(null);

// Scroll-aware cross-fade state
let wasScrolled = false;
let savedScrollTop = 0;
let enteringEl = null;
const SCROLL_FADE_THRESHOLD = 50;

// Dynamic header title based on current view
const headerTitle = computed(() => {
  const titles = {
    'home': t('settings.title'),
    'languages': t('settings.languages'),
    'apps': t('settings.dock'),
    'volume': t('settings.volume'),
    'screen': t('settings.screen'),
    'spotify': t('spotifySettings.title'),
    'multiroom': t('multiroom.title'),
    'multiroom-zone-edit': zoneGroupId.value
      ? t('dsp.zones.editZone', 'Edit Zone')
      : t('dsp.zones.createZone', 'Create Zone'),
    'multiroom-client-edit': t('multiroom.editSpeaker', 'Edit Speaker'),
    'radio': 'Radio',
    'radio-add': t('radio.manageStation.addStationTitle'),
    'radio-edit': t('radio.manageStation.editStationTitle'),
    'podcast': t('podcastSettings.title'),
    'macos': t('macSettings.title'),
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

// Check if the station can be restored (only modified stations)
const canRestoreStation = computed(() => {
  return stationToEdit.value?._canRestore === true;
});

// Check if the station can be deleted (only manually added stations)
const canDeleteStation = computed(() => {
  return stationToEdit.value?._canDelete === true;
});

// === Scroll-aware cross-fade transition hooks ===
// When scrolled: old content stays at scroll position (static), new content overlays at viewport
// position (absolute+top), header translates into viewport and fades in with the new content.
// On after-leave: inline styles + scroll reset cancel out for a seamless swap.

function onBeforeLeave(el) {
  const scrollEl = modalContentRef?.value;
  const scrollTop = scrollEl?.scrollTop || 0;

  if (scrollTop > SCROLL_FADE_THRESHOLD) {
    wasScrolled = true;
    savedScrollTop = scrollTop;
    const headerEl = headerRef.value?.$el;
    if (headerEl) {
      headerEl.style.transition = 'none';
      headerEl.style.opacity = '0';
      headerEl.style.transform = `translateY(${scrollTop}px)`;
    }
    el.style.position = 'static';
  } else {
    wasScrolled = false;
    savedScrollTop = 0;
    if (scrollTop > 0) resetScroll();
  }
}

function onEnter(el) {
  if (wasScrolled) {
    enteringEl = el;
    el.style.position = 'absolute';
    el.style.top = `${savedScrollTop}px`;
    el.style.left = '0';
    el.style.width = '100%';
    // Double rAF syncs with Vue's internal enter transition timing
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const headerEl = headerRef.value?.$el;
        if (headerEl) {
          headerEl.style.transition = '';
          headerEl.style.opacity = '';
        }
      });
    });
  }
}

function onAfterLeave() {
  if (wasScrolled && enteringEl) {
    enteringEl.style.position = '';
    enteringEl.style.top = '';
    enteringEl.style.left = '';
    enteringEl.style.width = '';
    const headerEl = headerRef.value?.$el;
    if (headerEl) {
      headerEl.style.transform = '';
    }
    resetScroll();
    enteringEl = null;
    wasScrolled = false;
  }
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
      logger.info('settings', 'Station deleted');

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
  logger.info('settings', 'Station added', station);
  // Reload RadioSettings data
  if (radioSettingsRef.value) {
    radioSettingsRef.value.loadCustomStations();
  }
  back();
}

// === MULTIROOM ZONE/CLIENT HANDLERS ===
function handleEditZone(groupId) {
  zoneGroupId.value = groupId;
  push('multiroom-zone-edit');
}

function handleCreateZone() {
  zoneGroupId.value = null;
  push('multiroom-zone-edit');
}

function handleEditClient(macId) {
  macIdToEdit.value = macId;
  push('multiroom-client-edit');
}

function handleZoneSaved() {
  zoneGroupId.value = null;
  back();
}

function handleClientSaved() {
  macIdToEdit.value = null;
  back();
}

async function handleRadioStationEdited(station) {
  logger.info('settings', 'Station edited', station);

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
  if (settingsStore.dockApps.spotify) count++;
  if (settingsStore.dockApps.mac) count++;
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

// Reset toggling when multiroom state actually changes
watch(isMultiroomActive, () => {
  isMultiroomToggling.value = false;
});

onMounted(async () => {
  // Preload all settings in parallel
  await Promise.all([
    i18n.initializeLanguage(),
    settingsStore.loadAllSettings()
  ]);

  // Note: Multiroom settings are now loaded directly by MultiroomSettings.vue via multiroomStore

  on('routing', 'multiroom_enabling', handleMultiroomEnabling);
  on('routing', 'multiroom_disabling', handleMultiroomDisabling);
});

</script>

<style scoped>
.settings-modal {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

:deep(.modal-header) {
  transition: padding var(--transition-fast), opacity var(--transition-in-out);
}

/* Cross-fade wrapper: positioning context for leaving element overlay */
.transition-wrapper {
  position: relative;
}

/* Cross-fade: leaving content overlays absolutely (doesn't affect height) */
:deep(.fade-slide-leave-active) {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
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
