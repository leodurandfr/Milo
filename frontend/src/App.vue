<!-- App.vue - Version with i18n WebSocket -->
<template>
  <div class="app-container">
    <!-- Notification banner for connection issues and errors (shown after boot) -->
    <NotificationBanner
      :title="notificationTitle"
      :detail="notificationDetail"
      :dismissable="isNotificationDismissable"
      @dismiss="dismissNotification"
    />

    <!-- Setup Wizard (blocks entire UI when setup not completed) -->
    <SetupWizard v-if="settingsStore.setupCompleted === false" />

    <!-- App content only renders after boot completes AND setup is done -->
    <template v-else-if="isBootComplete">
      <router-view />
      <VolumeBar />
      <Dock
        @open-equalizer="isEqualizerOpen = true"
        @open-multiroom="isMultiroomOpen = true"
        @open-settings="isSettingsOpen = true"
      />

      <Modal :is-open="isEqualizerOpen" @close="isEqualizerOpen = false">
        <EqualizerModal />
      </Modal>

      <Modal :is-open="isMultiroomOpen" @close="isMultiroomOpen = false">
        <MultiroomModal />
      </Modal>

      <Modal :is-open="isSettingsOpen" @close="closeSettings">
        <SettingsModal :initial-view="settingsInitialView" />
      </Modal>
    </template>

    <!-- Global Virtual Keyboard (available in wizard too) -->
    <VirtualKeyboard />

    <!-- Sleep shield: intercepts touch when screen is off to prevent accidental UI interaction -->
    <div
      v-if="settingsStore.isScreenSleeping && settingsStore.setupCompleted !== false"
      class="sleep-shield"
      @touchstart.stop.prevent="handleScreenWake"
      @touchend.stop.prevent
      @pointerdown.stop.prevent="handleScreenWake"
      @pointerup.stop.prevent
      @click.stop.prevent
    />

  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, provide, defineAsyncComponent } from 'vue';
import VolumeBar from '@/components/ui/VolumeBar.vue';
import Dock from '@/components/ui/Dock.vue';
import Modal from '@/components/ui/Modal.vue';
import NotificationBanner from '@/components/ui/NotificationBanner.vue';

// Lazy-loaded modals
const EqualizerModal = defineAsyncComponent(() =>
  import('@/components/equalizer/EqualizerModal.vue')
);
const MultiroomModal = defineAsyncComponent(() =>
  import('@/components/multiroom/MultiroomModal.vue')
);
const SettingsModal = defineAsyncComponent(() =>
  import('@/components/settings/SettingsModal.vue')
);
const VirtualKeyboard = defineAsyncComponent(() =>
  import('@/components/ui/VirtualKeyboard.vue')
);
const SetupWizard = defineAsyncComponent(() =>
  import('@/components/setup/SetupWizard.vue')
);

import axios from 'axios';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { usePodcastStore } from '@/stores/podcastStore';
import { useCdStore } from '@/stores/cdStore';
import { useRadioStore } from '@/stores/radioStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useEqualizerStore } from '@/stores/equalizerStore';
import { i18n, useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { logger } from '@/services/logger';
import { useScreenActivity } from '@/composables/useScreenActivity';
import { useHardwareConfig } from '@/composables/useHardwareConfig';

// === Constants ===
const BOOT_TIMEOUT_MS = 2000;        // Show "connecting" after 2s (roughly when attempt 2 starts)
const BOOT_FAILED_MS = 12000;        // Show "unavailable" after 12s total
const DOM_REMOVE_DELAY = 400;
const DOCK_AUTO_SHOW_DELAY = 1000;

// Fast boot: skip logo animation on refresh (user already saw it this session)
const isFastBoot = document.documentElement.classList.contains('fast-boot');
const LOGO_FADE_DELAY = isFastBoot ? 0 : 400;
const SCREEN_FADE_DELAY = isFastBoot ? 100 : 500;

const { t } = useI18n();
const unifiedStore = useUnifiedAudioStore();
const podcastStore = usePodcastStore();
const cdStore = useCdStore();
const radioStore = useRadioStore();
const settingsStore = useSettingsStore();
const multiroomStore = useMultiroomStore();
const equalizerStore = useEqualizerStore();
const { on, onReconnect, onVisibilityChange, isConnected } = useWebSocket();
const { loadHardwareInfo } = useHardwareConfig();

// Enable screen activity detection (touch, mouse, keyboard)
useScreenActivity();

// === State ===
const isReady = ref(false);
const isBootComplete = ref(false);
const currentError = ref(null);
const showConnectionLost = ref(false);
let connectionLostTimeout = null;

// iOS delay constant for connection lost notification
const IOS_CONNECTION_LOST_DELAY_MS = 1200;

// === Boot screen reference ===
let bootScreenEl = null;
let bootTimeoutId = null;
let bootFailedTimeoutId = null;

// Shared handler for initial state (WebSocket event or HTTP fallback)
function processInitialState(event) {
  clearBootTimeout();
  unifiedStore.updateState(event);

  if (event.data?.setup_completed !== undefined) {
    settingsStore.updateSetupCompleted(event.data.setup_completed);
  }
  if (event.data?.hotspot_active !== undefined) {
    settingsStore.updateHotspotActive(event.data.hotspot_active);
  }

  const fullState = event.data?.full_state;
  if (fullState?.active_source === 'podcast' && fullState?.metadata) {
    podcastStore.handleStateUpdate(fullState.metadata);
  }
  if (fullState?.active_source === 'cd' && fullState?.metadata) {
    cdStore.handleSourceEvent({ origin: 'cd', type: 'state_changed', data: { metadata: fullState.metadata } });
  }

  if (isBootComplete.value && showDockFn && unifiedStore.systemState.active_source === 'none') {
    showDockFn();
  }

  isReady.value = true;
}

// === Boot timeout handling ===
function startBootTimeout() {
  clearBootTimeout();
  // Stage 1: Show "connecting" after first timeout
  bootTimeoutId = setTimeout(() => {
    if (!isReady.value) {
      showBootMessage(t('app.connecting'));
      // HTTP fallback for captive portal (macOS doesn't support WebSocket)
      axios.get('/api/initial-state').then(({ data }) => {
        if (!isReady.value && data?.status === 'success') {
          processInitialState({ category: 'system', type: 'initial_state', source: 'system', data });
        }
      }).catch(() => {});
      // Stage 2: Show "unavailable" after more time
      bootFailedTimeoutId = setTimeout(() => {
        if (!isReady.value) {
          showBootMessage(t('app.connectionUnavailable'));
        }
      }, BOOT_FAILED_MS - BOOT_TIMEOUT_MS);
    }
  }, BOOT_TIMEOUT_MS);
}

function clearBootTimeout() {
  if (bootTimeoutId) {
    clearTimeout(bootTimeoutId);
    bootTimeoutId = null;
  }
  if (bootFailedTimeoutId) {
    clearTimeout(bootFailedTimeoutId);
    bootFailedTimeoutId = null;
  }
}

// === Boot message helpers ===
function showBootMessage(message) {
  if (!bootScreenEl) return;

  const textEl = bootScreenEl.querySelector('.boot-message-text');
  if (textEl) textEl.textContent = message;

  bootScreenEl.classList.add('show-message');
}

function hideBootMessage() {
  if (!bootScreenEl) return;
  bootScreenEl.classList.remove('show-message');
}

// === Notification banner (connection issues and errors after boot) ===
const capitalize = (s) => s ? s.charAt(0).toUpperCase() + s.slice(1) : '';

// Watch connection state with iOS-specific delay to avoid flash on quick reconnects
watch(isConnected, (connected) => {
  if (!isBootComplete.value) return;
  // Suppress during setup wizard (HTTP fallback handles captive portal without WebSocket)
  if (settingsStore.setupCompleted === false) return;

  if (connectionLostTimeout) {
    clearTimeout(connectionLostTimeout);
    connectionLostTimeout = null;
  }

  if (!connected) {
    const isStandalone = window.navigator.standalone === true
      || window.matchMedia('(display-mode: standalone)').matches;
    if (isStandalone) {
      // PWA standalone: delay to avoid flash during quick background/foreground transitions
      connectionLostTimeout = setTimeout(() => {
        if (!isConnected.value) {
          showConnectionLost.value = true;
        }
      }, IOS_CONNECTION_LOST_DELAY_MS);
    } else {
      // Desktop browser: show immediately
      showConnectionLost.value = true;
    }
  } else {
    showConnectionLost.value = false;
  }
});

const notificationTitle = computed(() => {
  // Priority 1: Connection lost (highest priority)
  if (showConnectionLost.value) {
    return t('notification.connectionLostTitle');
  }
  // Priority 2: System/source errors
  return currentError.value?.title || null;
});

const notificationDetail = computed(() => {
  // Priority 1: Connection lost description
  if (showConnectionLost.value) {
    return t('notification.connectionLostDescription');
  }
  // Priority 2: System/source error detail
  return currentError.value?.detail || null;
});

// Connection lost is not dismissable (auto-clears on reconnect)
// System/source errors are dismissable
const isNotificationDismissable = computed(() => {
  return !showConnectionLost.value && currentError.value !== null;
});

function dismissNotification() {
  currentError.value = null;
}

// Auto-show transient notification on command failure (play/pause/next/prev)
let commandErrorTimer = null;
watch(() => unifiedStore.commandError, (err) => {
  if (!err) return;
  unifiedStore.commandError = null;
  if (commandErrorTimer) clearTimeout(commandErrorTimer);
  const source = capitalize(err.source || 'audio');
  currentError.value = { title: `${source} · ${t('notification.commandFailed')}`, detail: err.command };
  commandErrorTimer = setTimeout(() => {
    if (currentError.value?.detail === err.command) {
      currentError.value = null;
    }
  }, 4000);
});

// === Sleep shield: wake screen on first touch ===
let sleepShieldTimeout = null;
let wakeInProgress = false;

function handleScreenWake() {
  if (wakeInProgress || !settingsStore.isScreenSleeping) return;
  wakeInProgress = true;

  // Send wake notification to backend (triggers on_touch_detected → broadcasts screen_sleep_changed: false)
  axios.post('/api/settings/screen-activity').catch(() => {});

  // Safety fallback: if WebSocket event doesn't arrive within 500ms, force-hide the shield
  clearTimeout(sleepShieldTimeout);
  sleepShieldTimeout = setTimeout(() => {
    if (settingsStore.isScreenSleeping) {
      settingsStore.updateScreenSleeping(false);
    }
    wakeInProgress = false;
  }, 500);
}

// Watch isReady → trigger boot screen fade and dock auto-show
watch(isReady, (ready) => {
  if (ready && bootScreenEl) {
    clearBootTimeout();
    hideBootMessage();

    if (!isFastBoot) {
      setTimeout(() => {
        bootScreenEl.classList.add('logo-exit');
      }, LOGO_FADE_DELAY);
    }

    setTimeout(() => {
      bootScreenEl.classList.add('fade-out');
      isBootComplete.value = true;
      sessionStorage.setItem('milo_booted', '1');

      setTimeout(() => {
        if (bootScreenEl) bootScreenEl.style.display = 'none';
      }, DOM_REMOVE_DELAY);

      // Auto-show dock after boot complete, only if no audio source is active
      setTimeout(() => {
        if (showDockFn && unifiedStore.systemState.active_source === 'none') {
          showDockFn();
        }
      }, DOCK_AUTO_SHOW_DELAY);
    }, SCREEN_FADE_DELAY);
  }
});

const isEqualizerOpen = ref(false);
const isMultiroomOpen = ref(false);
const isSettingsOpen = ref(false);

// Settings navigation - supports direct navigation to sub-views
const settingsInitialView = ref('home');

function openSettings(initialView = 'home') {
  settingsInitialView.value = initialView;
  isSettingsOpen.value = true;
}

function closeSettings() {
  isSettingsOpen.value = false;
  settingsInitialView.value = 'home'; // Reset for next open
}

// Dock control registration (for auto-show on boot)
let showDockFn = null;
function registerDockControl(showFn) {
  showDockFn = showFn;
}

// Provide for child components
provide('openEqualizer', () => isEqualizerOpen.value = true);
provide('openMultiroom', () => isMultiroomOpen.value = true);
provide('openSettings', openSettings);
provide('closeModals', () => {
  isEqualizerOpen.value = false;
  isMultiroomOpen.value = false;
  closeSettings();
});
provide('registerDockControl', registerDockControl);

const cleanupFunctions = [];

onMounted(async () => {
  // Initialize boot screen reference and start timeout
  bootScreenEl = document.getElementById('boot-screen');
  startBootTimeout();

  // Register WebSocket event listeners FIRST (before any async operations)
  // This prevents race condition where initial_state arrives before listeners are ready
  cleanupFunctions.push(
    on('system', 'initial_state', (event) => {
      if (!isReady.value) processInitialState(event);
    }),
    on('volume', 'volume_changed', (event) => unifiedStore.handleVolumeEvent(event)),
    on('system', 'state_changed', (event) => unifiedStore.updateState(event)),
    on('system', 'transition_start', (event) => unifiedStore.updateState(event)),
    on('system', 'transition_complete', (event) => unifiedStore.updateState(event)),
    on('source', 'state_changed', (event) => {
      unifiedStore.updateState(event);
      podcastStore.handleSourceEvent(event);
      cdStore.handleSourceEvent(event);
      // Display source error in notification banner
      if (event.data?.new_state === 'error') {
        const source = event.data?.source || 'source';
        const error = event.data?.metadata?.error || 'error';
        currentError.value = { title: `${capitalize(source)} Error`, detail: error };
      }
    }),
    on('source', 'position_update', (event) => {
      unifiedStore.updatePosition(event);
      podcastStore.handlePositionUpdate(event);
    }),
    on('source', 'error_cleared', () => {
      // Auto-dismiss error notification when the error condition is resolved
      currentError.value = null;
    }),
    on('system', 'error', (event) => {
      const source = event.data?.source || 'system';
      const message = event.data?.message || event.data?.error || 'Unknown error';
      currentError.value = { title: `${capitalize(source)} Error`, detail: message };
    }),
    on('system', 'backend_error', (event) => {
      const level = event.data?.level || 'ERROR';
      const message = event.data?.message || 'Backend error';
      currentError.value = { title: `${t('notification.backendErrorTitle')} · ${level === 'WARNING' ? 'Warning' : 'Error'}`, detail: message, source: 'backend' };
    }),
    on('system', 'cd_drive_status', (event) => {
      cdStore.handleSystemEvent(event);
    }),
    on('settings', 'language_changed', (event) => {
      if (event.data?.language) {
        i18n.handleLanguageChanged(event.data.language);
        settingsStore.updateLanguage(event.data.language);
      }
    }),
    on('settings', 'dock_apps_changed', (event) => {
      if (event.data?.config?.enabled_apps) {
        settingsStore.updateDockApps(event.data.config.enabled_apps);
      }
    }),
    on('settings', 'screen_sleep_changed', (event) => {
      if (event.data?.sleeping !== undefined) {
        settingsStore.updateScreenSleeping(event.data.sleeping);
        if (!event.data.sleeping) wakeInProgress = false;
      }
    }),
    // Routing transition events - centralized in multiroomStore
    on('routing', 'multiroom_enabling', (event) => multiroomStore.handleRoutingEvent(event)),
    on('routing', 'multiroom_disabling', (event) => multiroomStore.handleRoutingEvent(event)),
    on('routing', 'multiroom_ready', (event) => multiroomStore.handleRoutingEvent(event)),
    on('routing', 'multiroom_error', (event) => multiroomStore.handleRoutingEvent(event)),
    // Multiroom events - new standardized format (Story 6.2)
    on('multiroom', 'client_state_changed', (event) => multiroomStore.handleMultiroomEvent(event)),
    on('multiroom', 'zone_changed', (event) => multiroomStore.handleMultiroomEvent(event)),
    on('multiroom', 'pending_client_changed', (event) => {
      const isNew = event.data?.action === 'registered' &&
        !multiroomStore.pendingClients.has(event.data?.client?.mac_id);
      multiroomStore.handleMultiroomEvent(event);
      if (isNew && !isSettingsOpen.value) {
        openSettings('multiroom');
      }
    }),
    on('multiroom', 'equalizer_changed', (event) => equalizerStore.handleEqualizerChanged(event)),
    on('multiroom', 'crossover_changed', (event) => equalizerStore.handleZoneCrossoverChanged(event)),
    // Radio favorite events
    on('source', 'favorite_added', (event) => {
      if (event.data?.source === 'radio' && event.data?.station_id) {
        radioStore.handleFavoriteEvent(event.data.station_id, true);
      }
    }),
    on('source', 'favorite_removed', (event) => {
      if (event.data?.source === 'radio' && event.data?.station_id) {
        radioStore.handleFavoriteEvent(event.data.station_id, false);
      }
    }),
    on('source', 'favorite_modified', (event) => {
      if (event.data?.source === 'radio' && event.data?.station) {
        radioStore.handleMetadataModified(event.data.station);
      }
    }),
    // Settings events — centralized handlers for all settings changes
    on('settings', 'volume_limits_changed', (event) => {
      if (event.data?.limits) {
        settingsStore.updateVolumeLimits(event.data.limits);
      }
    }),
    on('settings', 'volume_startup_changed', (event) => {
      if (event.data?.config) {
        settingsStore.updateVolumeStartup(event.data.config);
      }
    }),
    on('settings', 'volume_steps_changed', (event) => {
      if (event.data?.config?.step_mobile_db !== undefined) {
        unifiedStore.updateMobileStep(event.data.config.step_mobile_db);
      }
    }),
    on('settings', 'rotary_steps_changed', (event) => {
      if (event.data?.config) {
        settingsStore.updateVolumeSteps(event.data.config);
      }
    }),
    on('settings', 'bt_remote_steps_changed', (event) => {
      if (event.data?.config) {
        settingsStore.updateVolumeSteps(event.data.config);
      }
    }),
    on('settings', 'spotify_disconnect_changed', (event) => {
      if (event.data?.config) {
        settingsStore.updateSpotifyDisconnect(event.data.config);
      }
    }),
    on('settings', 'screen_timeout_changed', (event) => {
      if (event.data?.config) {
        settingsStore.updateScreenTimeout(event.data.config);
      }
    }),
    on('settings', 'screen_brightness_changed', (event) => {
      if (event.data?.config) {
        settingsStore.updateScreenBrightness(event.data.config);
      }
    }),
    on('settings', 'screen_screensaver_changed', (event) => {
      if (event.data?.config) {
        settingsStore.updateScreenScreensaver(event.data.config);
      }
    }),
    on('settings', 'screen_ui_scale_changed', (event) => {
      if (event.data?.config?.ui_scale !== undefined) {
        settingsStore.updateScreenUiScale(event.data.config);
      }
    }),
    on('settings', 'radio_settings_changed', (event) => {
      if (event.data?.config) {
        settingsStore.updateRadioSettings(event.data.config);
      }
    }),
    on('settings', 'mac_roc_changed', (event) => {
      if (event.data?.config) {
        settingsStore.updateMacRocSettings(event.data.config);
      }
    }),
    on('settings', 'inactivity_timeout_changed', (event) => {
      if (event.data?.config) {
        settingsStore.updateInactivityTimeout(event.data.config);
      }
    }),
    on('settings', 'bt_remote_config_changed', (event) => {
      if (event.data?.config) {
        settingsStore.updateBtRemoteConfig(event.data.config);
      }
    }),
    on('settings', 'bt_remote_status_changed', (event) => {
      if (event.data) {
        settingsStore.updateBtRemoteStatus(event.data);
      }
    }),
    on('settings', 'podcast_credentials_changed', (event) => {
      if (event.data?.config) {
        settingsStore.updatePodcastCredentials({
          taddy_user_id: event.data.config.taddy_user_id || '',
          taddy_api_key: event.data.config.taddy_api_key || ''
        });
      }
    }),
    // Equalizer events
    on('equalizer', 'filter_changed', (event) => equalizerStore.handleFilterChanged(event)),
    on('equalizer', 'filters_reset', () => equalizerStore.handleFiltersReset()),
    on('equalizer', 'state_changed', (event) => equalizerStore.handleStateChanged(event)),
    on('equalizer', 'preset_loaded', (event) => equalizerStore.handlePresetLoaded(event)),
    on('equalizer', 'compressor_changed', (event) => equalizerStore.handleCompressorChanged(event)),
    on('equalizer', 'loudness_changed', (event) => equalizerStore.handleLoudnessChanged(event)),
    on('equalizer', 'mono_changed', (event) => equalizerStore.handleMonoChanged(event)),
    on('equalizer', 'enabled_changed', (event) => equalizerStore.handleEnabledChanged(event)),
    on('equalizer', 'zone_enabled_changed', (event) => equalizerStore.handleZoneEnabledChanged(event)),
    onReconnect(() => {
      logger.info('websocket', 'WebSocket reconnected');
      // Refresh registry state on reconnect (AC3: State Resync)
      multiroomStore.fetchState();
      // Refresh equalizer state for current target
      equalizerStore.loadStatus();
      // Refresh CD drive status (may have changed during disconnect)
      cdStore.fetchDriveStatus();
    }),
    onVisibilityChange(() => {
      // Refresh stores when tab becomes visible (fixes stale data after background)
      multiroomStore.fetchState();
      equalizerStore.loadStatus();
      cdStore.fetchDriveStatus();
    })
  );

  // Now perform async initialization
  await loadHardwareInfo();
  await settingsStore.loadAllSettings();

  // Load BT remote status in background (separate endpoint, may not be available)
  settingsStore.loadBtRemoteStatus();

  // Initialize client registry (loads from cache + fetches fresh state)
  multiroomStore.initialize();

  // Preload podcast subscriptions list in background (for instant hasSubscriptions check)
  // Only fetches local data, no Taddy API call - episodes loaded when HomeView opens
  podcastStore.preloadSubscriptionsList()

  // Preload radio favorites in background (for instant display when user opens Radio)
  radioStore.preloadFavorites()

  // Fetch CD drive status in background (for conditional dock display)
  cdStore.fetchDriveStatus();

  // Preload modals in background for instant display when user opens them
  Promise.all([
    import('@/components/equalizer/EqualizerModal.vue'),
    import('@/components/multiroom/MultiroomModal.vue'),
    import('@/components/settings/SettingsModal.vue'),
    import('@/components/ui/VirtualKeyboard.vue')
  ]);
});

onUnmounted(() => {
  clearBootTimeout();
  clearTimeout(sleepShieldTimeout);
  if (connectionLostTimeout) {
    clearTimeout(connectionLostTimeout);
    connectionLostTimeout = null;
  }
  cleanupFunctions.forEach(cleanup => cleanup());
});
</script>

<style>
.app-container {
  height: 100%;
}

.sleep-shield {
  position: fixed;
  inset: 0;
  z-index: 99999;
  touch-action: none;
  -webkit-tap-highlight-color: transparent;
  cursor: default;
}

/* Only show the sleep shield on the Raspberry Pi screen (landscape) */
@media (max-aspect-ratio: 4/3) {
  .sleep-shield {
    display: none !important;
  }
}
</style>