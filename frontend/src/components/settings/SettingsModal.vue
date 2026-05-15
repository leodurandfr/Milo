<!-- frontend/src/components/settings/SettingsModal.vue -->
<template>
  <div class="settings-modal">
    <!-- Single NavigationHeader outside transition -->
    <NavigationHeader ref="headerRef" :title="headerTitle" :show-back="canGoBack" :actions-key="currentView"
      @back="back">
      <template v-if="currentView === 'home' || currentView === 'multiroom' || stationActionIcon" #actions>
        <button v-if="currentView === 'home'" v-press class="power-toggle" @click="togglePowerMenu">
          <SvgIcon name="power" size="large" color="var(--color-text-contrast)"
            class="power-toggle__icon" :class="{ 'power-toggle__icon--hidden': showPowerMenu }" />
          <SvgIcon name="caretUp" size="large" color="var(--color-text-contrast)"
            class="power-toggle__icon" :class="{ 'power-toggle__icon--hidden': !showPowerMenu }" />
        </button>
        <Toggle v-if="currentView === 'multiroom'" :model-value="isMultiroomActive"
          :disabled="unifiedStore.systemState.transitioning || multiroomStore.isTransitioning" @change="handleMultiroomToggle" />
        <IconButton v-if="stationActionIcon" :icon="stationActionIcon" variant="on-dark"
          @click="toggleStationActionMenu" />
      </template>
    </NavigationHeader>

    <!-- Content area (wrapper provides positioning context for cross-fade overlay) -->
    <div class="transition-wrapper">
    <Transition name="fade-slide" @before-leave="onBeforeLeave" @enter="onEnter" @after-leave="onAfterLeave">
      <!-- Home view: list of categories -->
      <div v-if="currentView === 'home'" key="home" class="view-content home-view">
        <div class="power-menu-region" :class="{ 'power-menu-region--open': showPowerMenu }">
            <div class="power-menu-items">
              <ListItemButton @click="handleRestart">
                <template #icon>
                  <img :src="rebootIcon" alt="Restart" />
                </template>
                <template #title="{ headingClass }">
                  <span class="power-text-crossfade" :class="headingClass">
                    <span class="power-text" :class="{ 'power-text--active': !confirmRestart && !restartInProgress }">{{ t('settings.restart') }}</span>
                    <span class="power-text" :class="{ 'power-text--active': confirmRestart && !restartInProgress }">{{ t('settings.confirmRestart') }}</span>
                    <span class="power-text power-text--light" :class="{ 'power-text--active': restartInProgress }">{{ t('settings.restartInProgress') }}</span>
                  </span>
                </template>
              </ListItemButton>
              <ListItemButton @click="handleShutdown">
                <template #icon>
                  <img :src="shutdownIcon" alt="Shutdown" />
                </template>
                <template #title="{ headingClass }">
                  <span class="power-text-crossfade" :class="headingClass">
                    <span class="power-text" :class="{ 'power-text--active': !confirmShutdown && !shutdownInProgress }">{{ t('settings.shutdown') }}</span>
                    <span class="power-text" :class="{ 'power-text--active': confirmShutdown && !shutdownInProgress }">{{ t('settings.confirmShutdown') }}</span>
                    <span class="power-text power-text--light" :class="{ 'power-text--active': shutdownInProgress }">{{ t('settings.shutdownInProgress') }}</span>
                  </span>
                </template>
              </ListItemButton>
            </div>
        </div>
        <SettingsSection class="home-card">
          <div class="home-group">
            <span class="text-body settings-home-section-title">{{ t('settings.section.appearance') }}</span>
            <div class="settings-nav-grid">
              <ListItemButton variant="background" :title="t('settings.languages')" action="caret" @click="push('languages')">
                <template #icon>
                  <img :src="languagesIcon" alt="Languages" />
                </template>
              </ListItemButton>

              <ListItemButton variant="background" :title="t('settings.dock')" action="caret" @click="push('apps')">
                <template #icon>
                  <img :src="applicationsIcon" alt="Dock" />
                </template>
              </ListItemButton>

              <ListItemButton v-if="screenType !== 'none'" variant="background" :title="t('settings.screen')" action="caret" @click="push('screen')">
                <template #icon>
                  <img :src="displayIcon" alt="Display" />
                </template>
              </ListItemButton>
            </div>
          </div>

          <div class="home-group">
            <span class="text-body settings-home-section-title">{{ t('settings.section.audio') }}</span>
            <div class="settings-nav-grid">
              <ListItemButton v-if="unifiedStore.volumeState.any_volume_control" variant="background" :title="t('settings.volume')" action="caret" @click="push('volume')">
                <template #icon>
                  <img :src="volumeIcon" alt="Volume" />
                </template>
              </ListItemButton>

              <ListItemButton variant="background" :title="t('settings.audioPlayback')" action="caret" @click="push('audio-playback')">
                <template #icon>
                  <img :src="audioPlaybackIcon" alt="Audio playback" />
                </template>
              </ListItemButton>

              <ListItemButton variant="background" :title="t('settings.remoteControls')" action="caret" @click="push('remote-controls')">
                <template #icon>
                  <img :src="remoteControlsIcon" alt="Remote controls" />
                </template>
              </ListItemButton>

              <ListItemButton v-if="settingsStore.dockApps.multiroom" variant="background" :title="t('audioSources.multiroom')" action="caret"
                @click="push('multiroom')">
                <template #icon>
                  <img :src="multiroomIcon" alt="Multiroom" />
                </template>
              </ListItemButton>
            </div>
          </div>

          <div v-if="hasAnyConfigurableSource" class="home-group">
            <span class="text-body settings-home-section-title">{{ t('settings.section.sources') }}</span>
            <div class="settings-nav-grid">
              <ListItemButton v-if="settingsStore.dockApps.mac" variant="background" :title="t('audioSources.macOS')" action="caret"
                @click="push('macos')">
                <template #icon>
                  <img :src="macosIcon" alt="Mac" />
                </template>
              </ListItemButton>

              <ListItemButton v-if="settingsStore.dockApps.radio" variant="background" :title="t('audioSources.radio')" action="caret"
                @click="push('radio')">
                <template #icon>
                  <img :src="radioIcon" alt="Radio" />
                </template>
              </ListItemButton>

              <ListItemButton v-if="settingsStore.dockApps.podcast" variant="background" :title="t('audioSources.podcasts')" action="caret"
                @click="push('podcast')">
                <template #icon>
                  <img :src="podcastIcon" alt="Podcasts" />
                </template>
              </ListItemButton>
            </div>
          </div>

          <div class="home-group">
            <span class="text-body settings-home-section-title">{{ t('settings.section.system') }}</span>
            <div class="settings-nav-grid">
              <ListItemButton variant="background" :title="t('settings.network')" action="caret" @click="push('network')">
                <template #icon>
                  <img :src="networkIcon" alt="Network" />
                </template>
              </ListItemButton>

              <ListItemButton variant="background" :title="t('settings.hardware')" action="caret" @click="push('hardware')">
                <template #icon>
                  <img :src="hardwareIcon" alt="Hardware" />
                </template>
              </ListItemButton>

              <ListItemButton variant="background" :title="t('settings.updates')" action="caret" @click="push('updates')">
                <template #icon>
                  <img :src="updatesIcon" alt="Updates" />
                </template>
              </ListItemButton>

              <ListItemButton variant="background" :title="t('settings.information')" action="caret" @click="push('info')">
                <template #icon>
                  <img :src="informationIcon" alt="Information" />
                </template>
              </ListItemButton>
            </div>
          </div>
        </SettingsSection>
      </div>

      <!-- Languages view -->
      <LanguageSettings v-else-if="currentView === 'languages'" key="languages" class="view-content" />

      <!-- Dock view -->
      <DockSettings v-else-if="currentView === 'apps'" key="apps" class="view-content" />

      <!-- Volume view -->
      <VolumeSettings v-else-if="currentView === 'volume'" key="volume" class="view-content" />

      <!-- Screen view -->
      <ScreenSettings v-else-if="currentView === 'screen'" key="screen" class="view-content" />

      <!-- Network view -->
      <NetworkSettings v-else-if="currentView === 'network'" key="network" class="view-content" />

      <!-- Hardware view -->
      <HardwareSettings v-else-if="currentView === 'hardware'" key="hardware" class="view-content" />

      <!-- Audio playback view -->
      <AudioPlaybackSettings v-else-if="currentView === 'audio-playback'" key="audio-playback" class="view-content" />

      <!-- Remote controls view -->
      <RemoteControlsSettings v-else-if="currentView === 'remote-controls'" key="remote-controls" class="view-content"
        @open-bt-remote="push('bt-remote')" @open-ir-remote="push('ir-remote')" />

      <!-- BT remote sub-view -->
      <BtRemoteSettings v-else-if="currentView === 'bt-remote'" key="bt-remote" class="view-content" />

      <!-- IR remote sub-view -->
      <IrRemoteSettings v-else-if="currentView === 'ir-remote'" key="ir-remote" class="view-content"
        @open-hardware="push('hardware')" />

      <!-- Multiroom view -->
      <MultiroomSettings v-else-if="currentView === 'multiroom'" key="multiroom" class="view-content"
        @edit-zone="handleEditZone" @create-zone="handleCreateZone" @edit-client="handleEditClient"
        @configure-system="handleConfigureSystem" />

      <!-- Multiroom zone edit view -->
      <ZoneEdit v-else-if="currentView === 'multiroom-zone-edit'" key="multiroom-zone-edit" class="view-content"
        :group-id="zoneGroupId" :enable-client-renaming="true" @back="handleZoneSaved" @saved="handleZoneSaved" />

      <!-- Multiroom client edit view -->
      <ClientEdit v-else-if="currentView === 'multiroom-client-edit'" key="multiroom-client-edit" class="view-content"
        :mac-id="macIdToEdit" @back="handleClientSaved" />

      <!-- Multiroom configure pending speaker view -->
      <ConfigureSystem v-else-if="currentView === 'multiroom-configure-system'" key="multiroom-configure-system"
        class="view-content"
        :mac-id="macIdToEdit"
        :mode="hotspotToAdopt ? 'wifi' : 'ethernet'"
        :hotspot-ssid="hotspotToAdopt?.ssid ?? null"
        @back="handleConfigureSystemBack" />

      <!-- Radio view -->
      <RadioSettings v-else-if="currentView === 'radio'" key="radio" class="view-content"
        @go-to-add-station="push('radio-add')" @edit-station="handleEditStation" />

      <!-- Radio view - Add a station -->
      <ManageStation v-else-if="currentView === 'radio-add'" key="radio-add" class="view-content" mode="add"
        @back="handleBackFromRadioModal" @success="handleRadioStationAdded" />

      <!-- Radio view - Edit a station -->
      <ManageStation v-else-if="currentView === 'radio-edit'" key="radio-edit" class="view-content" mode="edit"
        :station="stationToEdit" :show-action-menu="showStationActionMenu"
        @back="handleBackFromRadioModal" @success="handleRadioStationEdited"
        @confirm-action="handleStationActionConfirm" />

      <!-- Podcast view -->
      <PodcastSettings v-else-if="currentView === 'podcast'" key="podcast" class="view-content" />

      <!-- Mac streaming view -->
      <MacSettings v-else-if="currentView === 'macos'" key="macos" class="view-content" />

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
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useRadioStore } from '@/stores/radioStore';
import { useNavigationStack } from '@/composables/useNavigationStack';
import { useViewTransition } from '@/composables/useViewTransition';
import { logger } from '@/services/logger';
import axios from 'axios';
import NavigationHeader from '@/components/ui/NavigationHeader.vue';
import Toggle from '@/components/ui/Toggle.vue';
import IconButton from '@/components/ui/IconButton.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import LanguageSettings from '@/components/settings/categories/LanguageSettings.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';

// Import settings icons
import languagesIcon from '@/assets/settings-icons/languages.svg';
import applicationsIcon from '@/assets/settings-icons/applications.svg';
import volumeIcon from '@/assets/settings-icons/volume.svg';
import displayIcon from '@/assets/settings-icons/display.svg';
import audioPlaybackIcon from '@/assets/settings-icons/audio-playback.svg';
import remoteControlsIcon from '@/assets/settings-icons/remote-controls.svg';
import multiroomIcon from '@/assets/settings-icons/multiroom.svg';
import updatesIcon from '@/assets/settings-icons/updates.svg';
import informationIcon from '@/assets/settings-icons/information.svg';
import radioIcon from '@/assets/settings-icons/radio.svg';
import podcastIcon from '@/assets/settings-icons/podcast.svg';
import macosIcon from '@/assets/settings-icons/macos.svg';
import hardwareIcon from '@/assets/settings-icons/hardware.svg';
import networkIcon from '@/assets/settings-icons/network.svg';
import rebootIcon from '@/assets/settings-icons/reboot.svg';
import shutdownIcon from '@/assets/settings-icons/shutdown.svg';
import DockSettings from '@/components/settings/categories/DockSettings.vue';
import VolumeSettings from '@/components/settings/categories/VolumeSettings.vue';
import ScreenSettings from '@/components/settings/categories/ScreenSettings.vue';
import AudioPlaybackSettings from '@/components/settings/categories/AudioPlaybackSettings.vue';
import RemoteControlsSettings from '@/components/settings/categories/RemoteControlsSettings.vue';
import BtRemoteSettings from '@/components/settings/categories/BtRemoteSettings.vue';
import IrRemoteSettings from '@/components/settings/categories/IrRemoteSettings.vue';
import MultiroomSettings from './categories/multiroom/MultiroomSettings.vue';
import ZoneEdit from './categories/multiroom/ZoneEdit.vue';
import ClientEdit from './categories/multiroom/ClientEdit.vue';
import ConfigureSystem from './categories/multiroom/ConfigureSystem.vue';
import RadioSettings from '@/components/settings/categories/radio/RadioSettings.vue';
import ManageStation from '@/components/settings/categories/radio/ManageStation.vue';
import PodcastSettings from '@/components/settings/categories/PodcastSettings.vue';
import MacSettings from '@/components/settings/categories/MacSettings.vue';
import HardwareSettings from '@/components/settings/categories/HardwareSettings.vue';
import UpdateManager from '@/components/settings/categories/UpdateManager.vue';
import InfoSettings from '@/components/settings/categories/InfoSettings.vue';
import NetworkSettings from '@/components/settings/categories/NetworkSettings.vue';
import { preloadWifiStatus } from '@/composables/useWifi';
import { preloadHardwareConfig, useHardwareConfig } from '@/composables/useHardwareConfig';

const { screenType } = useHardwareConfig();
const props = defineProps({
  initialView: {
    type: String,
    default: 'home'
  }
});

defineEmits(['close']);

const { t } = useI18n();
const settingsStore = useSettingsStore();
const unifiedStore = useUnifiedAudioStore();
const multiroomStore = useMultiroomStore();
const radioStore = useRadioStore();

// Inject modal refs for scroll detection and height pre-calculation
const modalContentRef = inject('modalContentRef', null);
const modalDeferScrollRestore = inject('modalDeferScrollRestore', null);
const modalContentInnerRef = inject('modalContentInnerRef', null);
const modalRequestHeightDelta = inject('modalRequestHeightDelta', null);
const modalCancelDeferredFinalize = inject('modalCancelDeferredFinalize', null);

// Navigation with scroll save/restore
const { currentView, canGoBack, push: navPush, back: navBack, reset, goTo, pendingScrollRestore } =
  useNavigationStack('home', { scrollElRef: modalContentRef });

// Refs
const headerRef = ref(null);
const stationToEdit = ref(null);
const zoneGroupId = ref(null);
const macIdToEdit = ref(null);
const hotspotToAdopt = ref(null);
const showPowerMenu = ref(false);
const confirmRestart = ref(false);
const confirmShutdown = ref(false);
const restartInProgress = ref(false);
const shutdownInProgress = ref(false);

// Scroll-aware view transition (shared with AudioSourceLayout via composable)
const { prepareNavigation, onBeforeLeave, onEnter, onAfterLeave } = useViewTransition({
  scrollElRef: modalContentRef,
  headerRef,
  pendingScrollRestore,
  onScrollRestored: () => { pendingScrollRestore.value = null; },
  deferScrollRestore: modalDeferScrollRestore,
  contentInnerRef: modalContentInnerRef,
  requestHeightDelta: modalRequestHeightDelta,
  cancelDeferred: modalCancelDeferredFinalize,
});

// Wrap push/back to pre-capture header clone.
// Called AFTER nav mutation so pendingScrollRestore is set, but BEFORE
// Vue re-renders the DOM (batched to next tick), so the clone captures old content.
function push(view, params) {
  showPowerMenu.value = false;
  confirmRestart.value = false;
  confirmShutdown.value = false;
  restartInProgress.value = false;
  shutdownInProgress.value = false;
  navPush(view, params);
  prepareNavigation();
}
function back() { navBack(); prepareNavigation(); }

// Dynamic header title based on current view
const headerTitle = computed(() => {
  const titles = {
    'home': t('settings.title'),
    'languages': t('settings.languages'),
    'apps': t('settings.dock'),
    'volume': t('settings.volume'),
    'screen': t('settings.screen'),
    'network': t('settings.network'),
    'hardware': t('settings.hardware'),
    'audio-playback': t('settings.audioPlayback'),
    'remote-controls': t('settings.remoteControls'),
    'bt-remote': t('settings.btRemote'),
    'ir-remote': t('settings.irRemote'),
    'multiroom': t('audioSources.multiroom'),
    'multiroom-zone-edit': zoneGroupId.value
      ? t('equalizer.zones.editZone')
      : t('equalizer.zones.createZone'),
    'multiroom-client-edit': t('multiroom.editSystem'),
    'multiroom-configure-system': t('multiroom.pending.configureTitle'),
    'radio': t('audioSources.radio'),
    'radio-add': t('radio.manageStation.addStationTitle'),
    'radio-edit': t('radio.manageStation.editStationTitle'),
    'podcast': t('audioSources.podcasts'),
    'macos': t('audioSources.macOS'),
    'updates': t('settings.updates'),
    'info': t('settings.information')
  };
  return titles[currentView.value] || t('settings.title');
});

// Navigate away from volume view when no device manages volume anymore
watch(() => unifiedStore.volumeState.any_volume_control, (hasControl) => {
  if (!hasControl && currentView.value === 'volume') back();
});

// Watch initialView prop for direct navigation (e.g., from CredentialsRequired)
watch(() => props.initialView, (newView) => {
  if (newView && newView !== 'home') {
    goTo(newView);
  } else {
    reset();
  }
}, { immediate: true });

// === Radio edit-view header action (Restore / Delete) ===
// Tapping the header IconButton toggles a confirm drawer (same pattern as the
// home power-menu). Tapping the ListItemButton in the drawer fires the action.

const showStationActionMenu = ref(false);

const stationActionIcon = computed(() => {
  if (currentView.value !== 'radio-edit' || !stationToEdit.value) return null;
  if (stationToEdit.value._canRestore) return 'arrowCounterClockwise';
  if (stationToEdit.value._canDelete) return 'trash';
  return null;
});

function toggleStationActionMenu() {
  showStationActionMenu.value = !showStationActionMenu.value;
}

function handleStationActionConfirm() {
  showStationActionMenu.value = false;
  if (stationToEdit.value?._canRestore) {
    handleRestoreStation();
  } else if (stationToEdit.value?._canDelete) {
    handleDeleteStation();
  }
}

// Close the drawer when leaving the edit view.
watch(currentView, (next) => {
  if (next !== 'radio-edit') showStationActionMenu.value = false;
});

// Radio navigation handling
function handleBackFromRadioModal() {
  back();
  stationToEdit.value = null;
}

function handleEditStation(station) {
  stationToEdit.value = station;
  push('radio-edit');
}

async function handleRestoreStation() {
  if (!stationToEdit.value) return;

  try {
    const formData = new FormData();
    formData.append('station_id', stationToEdit.value.id);

    const response = await axios.post('/api/radio/favorites/restore-metadata', formData);

    if (response.data.success) {
      // Wait a bit for backend to save
      await new Promise(resolve => setTimeout(resolve, 200));

      await radioStore.loadRadioSettingsData();
      back();
      stationToEdit.value = null;
    } else {
      logger.error('settings', 'Failed to restore station');
    }
  } catch (error) {
    logger.error('settings', 'Error restoring station:', error);
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

      await radioStore.loadRadioSettingsData();
      back();
      stationToEdit.value = null;
    } else {
      logger.error('settings', 'Failed to delete station');
    }
  } catch (error) {
    logger.error('settings', 'Error deleting station:', error);
  }
}

function handleRadioStationAdded(station) {
  logger.info('settings', 'Station added', station);
  radioStore.loadRadioSettingsData();
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

function handleConfigureSystem(payload) {
  // payload: { source: 'ethernet'|'wifi', macId?, ssid?, signal? }
  if (payload?.source === 'wifi') {
    hotspotToAdopt.value = {
      ssid: payload.ssid,
      signal: payload.signal
    };
    macIdToEdit.value = null;
  } else {
    macIdToEdit.value = payload?.macId ?? null;
    hotspotToAdopt.value = null;
  }
  push('multiroom-configure-system');
}

function handleConfigureSystemBack() {
  macIdToEdit.value = null;
  hotspotToAdopt.value = null;
  back();
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

  await radioStore.loadRadioSettingsData();
  stationToEdit.value = null;
  back();
}

// Power menu toggle
function togglePowerMenu() {
  showPowerMenu.value = !showPowerMenu.value;
  if (!showPowerMenu.value) {
    confirmRestart.value = false;
    confirmShutdown.value = false;
    restartInProgress.value = false;
    shutdownInProgress.value = false;
  }
}

// Power menu handlers
async function handleRestart() {
  if (restartInProgress.value) return;
  if (!confirmRestart.value) {
    confirmRestart.value = true;
    return;
  }
  restartInProgress.value = true;
  try {
    await axios.post('/api/system/restart');
  } catch (error) {
    logger.error('settings', 'Restart request failed', error);
    restartInProgress.value = false;
  }
}

async function handleShutdown() {
  if (shutdownInProgress.value) return;
  if (!confirmShutdown.value) {
    confirmShutdown.value = true;
    return;
  }
  shutdownInProgress.value = true;
  try {
    await axios.post('/api/system/shutdown');
  } catch (error) {
    logger.error('settings', 'Shutdown request failed', error);
    shutdownInProgress.value = false;
  }
}

// Hide the entire Sources section when none of its per-source entries are visible
const hasAnyConfigurableSource = computed(() =>
  settingsStore.dockApps.mac
  || settingsStore.dockApps.radio
  || settingsStore.dockApps.podcast
);

// Multiroom toggle
const isMultiroomActive = computed(() => unifiedStore.systemState.multiroom_enabled);

async function handleMultiroomToggle(enabled) {
  await unifiedStore.setMultiroomEnabled(enabled);
}

onMounted(async () => {
  // Preload all settings in parallel
  await Promise.all([
    i18n.initializeLanguage(),
    settingsStore.loadAllSettings()
  ]);

  // Refresh Taddy credentials status (external API call, podcast-only).
  // Guarded on 'unknown' so we only fetch once per frontend session — subsequent
  // modal opens reuse the cached status (App.vue watcher may have set it first).
  if (settingsStore.dockApps.podcast &&
      settingsStore.podcastCredentialsStatus === 'unknown') {
    settingsStore.refreshPodcastCredentialsStatus();
  }

  // Preload radio settings data if radio is enabled (non-blocking)
  if (settingsStore.dockApps.radio) {
    radioStore.loadRadioSettingsData();
  }

  // Preload wifi status for instant NetworkSettings rendering (non-blocking)
  preloadWifiStatus();

  // Preload hardware config for instant HardwareSettings rendering (non-blocking)
  preloadHardwareConfig();
});

</script>

<style scoped>
.settings-modal {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

:deep(.navigation-header) {
  position: relative;
  z-index: 1;
  transition: padding var(--transition-fast), opacity var(--transition-in-out);
}

/* Cross-fade wrapper: positioning context for leaving element overlay */
.transition-wrapper {
  position: relative;
}

/* Enter starts after leave finishes (sequential fade-out → fade-in) */
:deep(.fade-slide-enter-active) {
  transition-delay: 100ms;
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
  gap: var(--space-02);
}

.home-view {
  gap: 0;
}

/* Power toggle button */
.power-toggle {
  display: grid;
  place-items: center;
  background: var(--color-background-neutral-12);
  border: none;
  border-radius: var(--radius-04);
  padding: 8px;
  cursor: pointer;
  transition: var(--transition-press);
}

.power-toggle__icon {
  grid-row: 1;
  grid-column: 1;
  transition: opacity var(--transition-fast);
}

.power-toggle__icon--hidden {
  opacity: 0;
}

/* Power menu region (height animation without overflow clipping) */
.power-menu-region {
  max-height: 0;
  overflow: visible;
  transition: max-height var(--transition-fast);
}

.power-menu-region--open {
  max-height: 70px;
}

.power-menu-items {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-01);
  opacity: 0;
  transform: translateY(-100%);
  transition: opacity var(--transition-medium), transform var(--transition-medium);
}

.power-menu-region--open .power-menu-items {
  opacity: 1;
  transform: translateY(0);
  padding-bottom: var(--space-02);
}

.power-menu-items :deep(.list-item-button) {
  background: var(--color-background-neutral-50);
}

/* Power button text crossfade */
.power-text-crossfade {
  display: grid;
}

.power-text {
  grid-row: 1;
  grid-column: 1;
  opacity: 0;
  transition: opacity var(--transition-fast);
}

.power-text--active {
  opacity: 1;
}

.power-text--light {
  color: var(--color-text-secondary);
}

/* Override SettingsSection's default 16px gap to 24px for the denser home grid */
.home-card {
  gap: var(--space-05);
}

.home-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.settings-home-section-title {
  color: var(--color-text-secondary);
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

  .power-menu-region--open {
    max-height: 130px;
  }

  .power-menu-items {
    grid-template-columns: 1fr;
  }
}
</style>
