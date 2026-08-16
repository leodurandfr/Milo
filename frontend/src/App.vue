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

    <!-- Hostname conflict takes priority over everything (incl. setup wizard) -->
    <HostnameConflictView v-if="isBootComplete && systemStore.hostnameConflict" />

    <!-- Setup Wizard (blocks entire UI when setup not completed) -->
    <SetupWizard v-else-if="settingsStore.setupCompleted === false" />

    <!-- App content only renders after boot completes AND setup is done -->
    <template v-else-if="isBootComplete">
      <router-view />
      <VolumeBar />
      <Dock
        v-if="showChrome"
        @open-equalizer="isEqualizerOpen = true"
        @open-multiroom="isMultiroomOpen = true"
        @open-lyrics="lyricsStore.open()"
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

    <!-- Warm color filter overlay (Night Shift-like, configurable in screen settings).
         Teleported to <body> so the multiply blend reaches teleported elements
         (Dropdown menus, mobile AudioPlayer) that are siblings of #app. -->
    <Teleport to="body">
      <div
        v-if="colorFilterActive && showChrome"
        class="color-filter-overlay"
        :style="colorFilterStyle"
      />
    </Teleport>

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
import { useRoute } from 'vue-router';
import VolumeBar from '@/components/ui/VolumeBar.vue';
import Dock from '@/components/ui/Dock.vue';
import Modal from '@/components/ui/Modal.vue';
import NotificationBanner from '@/components/ui/NotificationBanner.vue';

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
const HostnameConflictView = defineAsyncComponent(() =>
  import('@/components/system/HostnameConflictView.vue')
);

import { apiCall } from '@/services/apiCall';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useLyricsStore } from '@/stores/lyricsStore';
import { usePodcastStore } from '@/stores/podcastStore';
import { useRadioStore } from '@/stores/radioStore';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useEqualizerStore } from '@/stores/equalizerStore';
import { useSystemStore } from '@/stores/systemStore';
import { useFanStore } from '@/stores/fanStore';
import { useUpdatesStore } from '@/stores/updatesStore';
import { i18n, useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { wsEventRegistry } from '@/schemas/ws';
import { logger } from '@/services/logger';
import { isKiosk } from '@/utils/kiosk';
import { useScreenActivity } from '@/composables/useScreenActivity';
import { useHardwareConfig } from '@/composables/useHardwareConfig';
import { useTimer } from '@/composables/useTimer';
import { handleNetworkStatusChanged, preloadNetworkStatus } from '@/composables/useNetwork';

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
const route = useRoute();
const unifiedStore = useUnifiedAudioStore();
const lyricsStore = useLyricsStore();
const podcastStore = usePodcastStore();
const radioStore = useRadioStore();
const musicLibraryStore = useMusicLibraryStore();
const settingsStore = useSettingsStore();
const multiroomStore = useMultiroomStore();
const equalizerStore = useEqualizerStore();
const systemStore = useSystemStore();
const fanStore = useFanStore();
const updatesStore = useUpdatesStore();
const { on, parsedOn, onReconnect, onVisibilityChange, isConnected } = useWebSocket();
const { loadHardwareInfo } = useHardwareConfig();
const timer = useTimer();

// Enable screen activity detection (touch, mouse, keyboard)
useScreenActivity();

// The locale derives from settingsStore.language (single source of truth):
// every writer (WS language_changed, bulk load, resync) converges here.
// main.js seeds i18n before mount, so no immediate run is needed.
watch(() => settingsStore.language, (lang) => i18n.handleLanguageChanged(lang));

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
    podcastStore.handleInitialMetadata(fullState.metadata);
  }

  isReady.value = true;
}

// Stores whose WS-fed state is delta-based: events missed while disconnected or
// backgrounded leave them stale until refetched. Each exposes a uniform resync();
// a new delta-based store MUST implement resync() and be listed here.
const deltaStores = [
  unifiedStore, multiroomStore, equalizerStore, systemStore, fanStore,
  radioStore, podcastStore, updatesStore, settingsStore,
  musicLibraryStore,
];

async function resyncStores() {
  // The central mirror first and ALONE. Every source store's now-playing slice
  // is a view of unifiedStore.systemState — radio and music_library compute
  // theirs from it, podcast copies it into its own refs on each delta and
  // re-applies the snapshot in its resync(). Healed in the same batch, that
  // re-application would read the pre-resync mirror and put back exactly the
  // stale episode the heal exists to replace.
  await unifiedStore.resync();
  await Promise.allSettled([
    ...deltaStores.filter((store) => store !== unifiedStore).map((store) => store.resync()),
    // Network status is a module-level singleton (useNetwork), not a store, but
    // its `status_changed` deltas are equally missable — heal it alongside.
    preloadNetworkStatus({ force: true }),
  ]);
}

// === Boot timeout handling ===
function startBootTimeout() {
  clearBootTimeout();
  // Stage 1: Show "connecting" after first timeout
  bootTimeoutId = timer.setTimeout(async () => {
    if (!isReady.value) {
      showBootMessage(t('app.connecting'));
      // HTTP fallback for captive portal (macOS doesn't support WebSocket)
      const result = await apiCall.get('/api/initial-state', {
        category: 'boot',
        message: 'Initial-state HTTP fallback failed',
        logLevel: 'debug',
      });
      if (!isReady.value && result.ok && result.data?.status === 'success') {
        processInitialState({ category: 'system', type: 'initial_state', source: 'system', data: result.data });
      }
      // Stage 2: Show "unavailable" after more time
      bootFailedTimeoutId = timer.setTimeout(() => {
        if (!isReady.value) {
          showBootMessage(t('app.connectionUnavailable'));
        }
      }, BOOT_FAILED_MS - BOOT_TIMEOUT_MS);
    }
  }, BOOT_TIMEOUT_MS);
}

function clearBootTimeout() {
  if (bootTimeoutId) {
    timer.clear(bootTimeoutId);
    bootTimeoutId = null;
  }
  if (bootFailedTimeoutId) {
    timer.clear(bootFailedTimeoutId);
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
    timer.clear(connectionLostTimeout);
    connectionLostTimeout = null;
  }

  if (!connected) {
    const isStandalone = window.navigator.standalone === true
      || window.matchMedia('(display-mode: standalone)').matches;
    if (isStandalone) {
      // PWA standalone: delay to avoid flash during quick background/foreground transitions
      connectionLostTimeout = timer.setTimeout(() => {
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

// Connectivity is deliberately absent from this banner. A missing link is only
// worth telling the user about when it blocks the source they selected, and
// that judgement needs the source — which the status card has and the banner
// does not. The card says it (see useSourceStatusDisplay); raising it here as
// well fired while listening over Bluetooth or a CD, where nothing was wrong.
const notificationTitle = computed(() => {
  // Priority 1: Connection lost (WS to backend down — local UI is stale)
  if (showConnectionLost.value) {
    return t('notification.connectionLostTitle');
  }
  // Priority 2: System/source errors
  return currentError.value?.title || null;
});

const notificationDetail = computed(() => {
  if (showConnectionLost.value) {
    return t('notification.connectionLostDescription');
  }
  return currentError.value?.detail || null;
});

// Connection lost auto-resolves when the socket comes back, so it isn't
// dismissable. Transient command/system errors are.
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
  if (commandErrorTimer) timer.clear(commandErrorTimer);
  const source = capitalize(err.source || 'audio');
  currentError.value = { title: `${source} · ${t('notification.commandFailed')}`, detail: err.command };
  commandErrorTimer = timer.setTimeout(() => {
    if (currentError.value?.detail === err.command) {
      currentError.value = null;
    }
  }, 4000);
});

// Auto-show generic transient notices ({ title, detail }) pushed by any feature
let transientNoticeTimer = null;
watch(() => unifiedStore.transientNotice, (notice) => {
  if (!notice) return;
  unifiedStore.transientNotice = null;
  if (transientNoticeTimer) timer.clear(transientNoticeTimer);
  currentError.value = { title: notice.title, detail: notice.detail || null };
  transientNoticeTimer = timer.setTimeout(() => {
    if (currentError.value?.title === notice.title) {
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
  apiCall.post('/api/settings/screen-activity', null, {
    category: 'screen',
    message: 'Screen-activity wake failed',
    logLevel: 'debug',
  });

  // Safety fallback: if WebSocket event doesn't arrive within 500ms, force-hide the shield
  timer.clear(sleepShieldTimeout);
  sleepShieldTimeout = timer.setTimeout(() => {
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
      timer.setTimeout(() => {
        bootScreenEl.classList.add('logo-exit');
      }, LOGO_FADE_DELAY);
    }

    timer.setTimeout(() => {
      bootScreenEl.classList.add('fade-out');
      isBootComplete.value = true;
      sessionStorage.setItem('milo_booted', '1');

      timer.setTimeout(() => {
        if (bootScreenEl) bootScreenEl.style.display = 'none';
      }, DOM_REMOVE_DELAY);

      // Auto-show dock after boot complete, only if no audio source is active
      timer.setTimeout(() => {
        if (showDockFn && unifiedStore.systemState.active_source === 'none') {
          showDockFn();
        }
      }, DOCK_AUTO_SHOW_DELAY);
    }, SCREEN_FADE_DELAY);
  }
});

// === Warm color filter overlay (Night Shift-like) ===
// Only applied on the Pi kiosk (localhost). Remote browsers (e.g. milo.local
// from another device) keep their native colors — same pattern as ui_scale.
const COLOR_FILTER_MAX_ALPHA = 0.40;

// App furniture the current route may opt out of: the Dock and the warm colour
// overlay. Declared by the route (`meta.chrome: false`) rather than matched on a
// path here, so the shell carries no knowledge of which page wants it — today
// only /components does, where both would sit over the component being judged.
const showChrome = computed(() => route.meta.chrome !== false);

const colorFilterActive = computed(() => {
  if (!isKiosk()) return false;
  const cf = settingsStore.screenColorFilter;
  return cf?.enabled && cf?.warmth > 0;
});

const colorFilterStyle = computed(() => {
  const warmth = settingsStore.screenColorFilter?.warmth ?? 0;
  const alpha = (warmth / 100) * COLOR_FILTER_MAX_ALPHA;
  return { backgroundColor: `rgba(255, 119, 0, ${alpha})` };
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

// Dock control registration (for auto-show on boot). The Dock hands it back on
// unmount — `null` — so a route that drops the chrome cannot leave this holding
// a callback whose component is gone.
let showDockFn = null;
function registerDockControl(showFn) {
  showDockFn = showFn;
}

// Signal to dismiss screensaver from App.vue (incremented to trigger watch in MainView)
const dismissScreensaverSignal = ref(0);

provide('openEqualizer', () => isEqualizerOpen.value = true);
provide('openMultiroom', () => isMultiroomOpen.value = true);
provide('openLyrics', () => lyricsStore.open());
provide('openSettings', openSettings);
provide('closeModals', () => {
  isEqualizerOpen.value = false;
  isMultiroomOpen.value = false;
  lyricsStore.close();
  closeSettings();
});
provide('registerDockControl', registerDockControl);
provide('dismissScreensaver', dismissScreensaverSignal);

// === WebSocket dispatch tables ===
// 46 of the 64 subscriptions do one thing: hand a payload to a store. Written
// out, they were 160 lines in which the only varying parts were the event name
// and the mutator — so they live here as data, and a new event is one row.
// The arms registered further down are the ones that genuinely do more: read a
// discriminator, drive the notification banner, or touch app-level UI state.

/**
 * Connectivity feeds two stores from one event: the raw NM level (Settings ›
 * Network reads it), and the injected full_state, which carries the recomputed
 * `network_unavailable` for the active source. Losing internet blocks a source
 * without anything about the source itself changing, so this is the only event
 * that can move the status card to "no internet".
 */
function handleConnectivityChanged(event) {
  systemStore.handleConnectivityEvent(event);
  unifiedStore.updateState(event);
}

/** Store handlers taking the whole event: [category, type, handler]. */
const RAW_EVENTS = [
  ['volume', 'volume_changed', unifiedStore.handleVolumeEvent],
  ['system', 'state_changed', unifiedStore.updateState],
  ['system', 'transition_start', unifiedStore.updateState],
  ['system', 'transition_complete', unifiedStore.updateState],
  // Drive-status changes carry full_state; apply it so the central mirror
  // (and thus the derived cdStore) reflects drive_connected/disc presence.
  ['system', 'cd_drive_status', unifiedStore.updateState],
  ['system', 'hostname_conflict_changed', systemStore.handleConflictEvent],
  ['system', 'connectivity_changed', handleConnectivityChanged],
  // Live network status (cable plug/unplug, wifi associate/dissociate), pushed
  // whenever the NM dispatcher signals a physical link change.
  ['network', 'status_changed', handleNetworkStatusChanged],
  ['routing', 'multiroom_enabling', multiroomStore.handleRoutingEvent],
  ['routing', 'multiroom_disabling', multiroomStore.handleRoutingEvent],
  ['routing', 'multiroom_ready', multiroomStore.handleRoutingEvent],
  ['routing', 'multiroom_error', multiroomStore.handleRoutingEvent],
  ['multiroom', 'client_state_changed', multiroomStore.handleMultiroomEvent],
  ['multiroom', 'zone_changed', multiroomStore.handleMultiroomEvent],
  ['equalizer', 'enabled_changed', equalizerStore.handleEnabledChanged],
  ['equalizer', 'zone_enabled_changed', equalizerStore.handleZoneEnabledChanged],
  // Storage spaces music is browsed from: a USB key plugged in or pulled, a
  // share written, and the counts growing while Navidrome indexes.
  ['source', 'storages_changed', musicLibraryStore.handleStoragesEvent],
];

/**
 * Zod-validated handlers taking the parsed payload: [category, type, handler].
 * The schema is derived from the pair, so a subscription can no longer be
 * validated against another event's schema.
 */
const PARSED_EVENTS = [
  ['source', 'position_update', unifiedStore.updatePosition],
  ['multiroom', 'equalizer_changed', equalizerStore.handleEqualizerChanged],
  ['settings', 'fan_config_changed', fanStore.applyConfig],
  ['settings', 'fan_status_changed', fanStore.applyTelemetry],
  ['equalizer', 'state_changed', equalizerStore.handleStateChanged],
  ['equalizer', 'levels', equalizerStore.handleLevelsChanged],
  ['programs', 'program_update_progress', updatesStore.handleProgramUpdateProgress],
  ['programs', 'program_update_complete', updatesStore.handleProgramUpdateComplete],
  ['programs', 'satellite_update_progress', updatesStore.handleSatelliteUpdateProgress],
  ['programs', 'satellite_update_complete', updatesStore.handleSatelliteUpdateComplete],
  ['programs', 'satellite_app_update_progress', updatesStore.handleSatelliteAppUpdateProgress],
  ['programs', 'satellite_app_update_complete', updatesStore.handleSatelliteAppUpdateComplete],
  ['programs', 'satellite_camilladsp_update_progress', updatesStore.handleSatelliteCamillaUpdateProgress],
  ['programs', 'satellite_camilladsp_update_complete', updatesStore.handleSatelliteCamillaUpdateComplete],
];

/**
 * `settings` events whose payload is a plain config blob: [type, mutator].
 * The category's shape is declared once on the backend
 * (core/models/settings_config.py) and shared by GET /api/settings/bulk.
 */
const SETTINGS_CONFIG_EVENTS = [
  ['volume_startup_changed', settingsStore.updateVolumeStartup],
  ['rotary_steps_changed', settingsStore.updateVolumeSteps],
  ['bt_remote_steps_changed', settingsStore.updateVolumeSteps],
  ['ir_remote_steps_changed', settingsStore.updateVolumeSteps],
  ['audio_stop_changed', settingsStore.updateAudioPlayback],
  ['screen_timeout_changed', settingsStore.updateScreenTimeout],
  ['screen_brightness_changed', settingsStore.updateScreenBrightness],
  ['screen_screensaver_changed', settingsStore.updateScreenScreensaver],
  ['screen_color_filter_changed', settingsStore.updateScreenColorFilter],
  ['radio_settings_changed', settingsStore.updateRadioSettings],
  ['music_library_settings_changed', settingsStore.updateMusicLibrarySettings],
  ['qobuz_settings_changed', settingsStore.updateQobuzSettings],
  ['spotify_settings_changed', settingsStore.updateSpotifySettings],
  ['mac_roc_changed', settingsStore.updateMacRocSettings],
  ['bt_remote_config_changed', settingsStore.updateBtRemoteConfig],
];

const cleanupFunctions = [];

onMounted(async () => {
  // Initialize boot screen reference and start timeout
  bootScreenEl = document.getElementById('boot-screen');
  startBootTimeout();

  // Register WebSocket event listeners FIRST (before any async operations)
  // This prevents race condition where initial_state arrives before listeners are ready
  cleanupFunctions.push(
    ...RAW_EVENTS.map(([category, type, handler]) => on(category, type, handler)),
    ...PARSED_EVENTS.map(([category, type, handler]) =>
      parsedOn(category, type, wsEventRegistry[`${category}.${type}`], handler)),
    ...SETTINGS_CONFIG_EVENTS.map(([type, apply]) => on('settings', type, (event) => {
      if (event.data?.config) apply(event.data.config);
    })),

    // No isReady guard: the backend re-sends initial_state on every reconnect
    // (ready handshake) and that snapshot heals state missed while offline
    on('system', 'initial_state', (event) => processInitialState(event)),
    on('source', 'state_changed', (event) => {
      unifiedStore.updateState(event);
      podcastStore.handleSourceEvent(event);
    }),
    // An operation failed on a source that survives it (a station that won't
    // tune, a command the daemon refused) — banner only. A source that is
    // *down* arrives as source_state 'error' in full_state and is drawn by the
    // status card instead, so neither is inferred from the other.
    on('source', 'error', (event) => {
      const source = event.data?.source || 'source';
      currentError.value = {
        title: t('notification.sourceErrorTitle', { source: capitalize(source) }),
        detail: event.data?.message || 'error',
        source,
      };
    }),
    on('source', 'error_cleared', (event) => {
      // Auto-dismiss the error notification, but only if the displayed error
      // came from the source that cleared (don't eat unrelated banners)
      if (currentError.value?.source === event.data?.source) {
        currentError.value = null;
      }
    }),
    on('system', 'error', (event) => {
      const source = event.data?.source || 'system';
      const message = event.data?.message || 'Unknown error';
      currentError.value = {
        title: t('notification.sourceErrorTitle', { source: capitalize(source) }),
        detail: message,
      };
    }),
    on('system', 'backend_error', (event) => {
      const message = event.data?.message || 'Backend error';
      currentError.value = { title: t('notification.backendErrorTitle'), detail: message, source: 'backend' };
    }),
    on('settings', 'language_changed', (event) => {
      if (event.data?.language) {
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
    on('settings', 'volume_limits_changed', (event) => {
      if (event.data?.limits) {
        settingsStore.updateVolumeLimits(event.data.limits);
      }
    }),
    // The mobile step is the one volume setting the audio store owns, not settings.
    on('settings', 'volume_steps_changed', (event) => {
      if (event.data?.config?.step_mobile_db !== undefined) {
        unifiedStore.updateMobileStep(event.data.config.step_mobile_db);
      }
    }),
    on('settings', 'screen_ui_scale_changed', (event) => {
      if (event.data?.config?.ui_scale !== undefined) {
        settingsStore.updateScreenUiScale(event.data.config);
      }
    }),
    // Both remote-status payloads are the event data itself, not a config blob.
    on('settings', 'bt_remote_status_changed', (event) => {
      if (event.data) {
        settingsStore.updateBtRemoteStatus(event.data);
      }
    }),
    on('settings', 'ir_remote_status_changed', (event) => {
      if (event.data) {
        settingsStore.applyIrRemoteStatus(event.data);
      }
    }),
    on('multiroom', 'pending_client_changed', (event) => {
      // isInitialized gates the classification, not the store update: the
      // subscription is installed before the first fetch (deliberately — it is
      // what stops events being missed during boot), so until it lands the map
      // is empty and every heartbeat of a long-known satellite reads as a brand
      // new one. A satellite re-registers every 15 s, so that window reliably
      // wakes the screen and opens Settings as the boot animation ends.
      const isNew = multiroomStore.isInitialized &&
        event.data?.action === 'registered' &&
        !multiroomStore.pendingClients.has(event.data?.client?.mac_id);
      multiroomStore.handleMultiroomEvent(event);
      if (isNew && !isSettingsOpen.value) {
        // Wake screen if sleeping
        if (settingsStore.isScreenSleeping) {
          apiCall.post('/api/settings/screen-activity', null, {
            category: 'screen',
            message: 'Screen-activity wake failed',
            logLevel: 'debug',
          });
          settingsStore.updateScreenSleeping(false);
        }
        // Dismiss screensaver
        dismissScreensaverSignal.value++;
        openSettings('multiroom');
      }
    }),
    // Favorite events (data.source discriminator: radio stations, podcast subscriptions)
    on('source', 'favorite_added', (event) => {
      if (event.data?.source === 'radio' && event.data?.station_id) {
        radioStore.handleFavoriteEvent(event.data.station_id, true);
      } else if (event.data?.source === 'podcast' && event.data?.podcast) {
        podcastStore.addSubscription(event.data.podcast);
      }
    }),
    on('source', 'favorite_removed', (event) => {
      if (event.data?.source === 'radio' && event.data?.station_id) {
        radioStore.handleFavoriteEvent(event.data.station_id, false);
      } else if (event.data?.source === 'podcast' && event.data?.uuid) {
        podcastStore.removeSubscription(event.data.uuid);
      }
    }),
    on('source', 'favorite_modified', (event) => {
      if (event.data?.source === 'radio' && event.data?.station) {
        radioStore.handleMetadataModified(event.data.station);
      }
    }),

    onReconnect(() => {
      logger.info('websocket', 'WebSocket reconnected');
      resyncStores();
    }),
    onVisibilityChange(() => {
      // Tab back to foreground with a live socket: refetch delta-based stores
      resyncStores();
    })
  );

  // Now perform async initialization
  await loadHardwareInfo();

  // Show the last known client registry before any request lands
  multiroomStore.primeFromCache();

  // Boot goes through the same recipe as a reconnect and a tab return: one
  // description of what the stores hold, in resyncStores(). A second,
  // hand-written boot list is how `pendingClients` ended up loaded on every
  // path except the first one — a store added here and forgotten there (or
  // the reverse) fails silently, since both look like they populate the app.
  await resyncStores();

  // Preload modals in background for instant display when user opens them
  Promise.all([
    import('@/components/equalizer/EqualizerModal.vue'),
    import('@/components/multiroom/MultiroomModal.vue'),
    import('@/components/lyrics/LyricsView.vue'),
    import('@/components/settings/SettingsModal.vue'),
    import('@/components/ui/VirtualKeyboard.vue')
  ]);
});

onUnmounted(() => {
  // All component timers (boot, sleep-shield, connection-lost, command-error)
  // are auto-cleared by useTimer.
  cleanupFunctions.forEach(cleanup => cleanup());
});
</script>

<style>
.app-container {
  height: 100%;
}

.color-filter-overlay {
  position: fixed;
  inset: 0;
  pointer-events: none;
  mix-blend-mode: multiply;
  z-index: 9999;
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