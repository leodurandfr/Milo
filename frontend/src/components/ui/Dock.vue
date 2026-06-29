<!-- frontend/src/components/ui/Dock.vue -->
<template>
  <!-- Invisible drag zone -->
  <div ref="dragZone" class="drag-zone" :class="{ dragging: isDragging }" @click.stop="onDragZoneClick"></div>

  <!-- Drag indicator -->
  <div class="dock-indicator" :class="{ hidden: isVisible, visible: showDragIndicator }" @click.stop="onIndicatorClick">
  </div>

  <!-- Navigation dock -->
  <nav ref="dockContainer" class="dock-container" :class="{ visible: isVisible, 'fully-visible': isFullyVisible }">
    <!-- Additional Apps - Mobile only -->
    <div v-if="additionalAppsInDOM && additionalDockApps.length > 0" ref="additionalAppsContainer"
      class="additional-apps-container glass-surface glass-border mobile-only" :class="{ visible: showAdditionalApps }">

      <button v-for="(app, index) in additionalDockApps.slice().reverse()" :key="app.id"
        @click="() => handleAdditionalAppClick(app.id)" v-press
        :style="{ '--stagger': `${0.05 + (additionalDockApps.length - 1 - index) * 0.02}s` }"
        class="additional-app-content glass-border button-interactive-subtle">
        <AppIcon :name="app.icon" :size="32" />
        <div class="app-title heading-2">{{ getAppTitle(app.id) }}</div>
      </button>
    </div>

    <div ref="dock" class="dock glass-surface glass-border">
      <!-- Volume Controls - Mobile only (hidden when no device manages volume) -->
      <div v-if="unifiedStore.volumeState.any_volume_control" class="volume-controls mobile-only" :style="{ transitionDelay: getDockItemDelay(0) }">
        <button v-for="{ icon, delta } in volumeControlsWithSteps" :key="icon"
          @pointerdown="(e) => onVolumeHoldStart(delta, e)" @pointerup="onVolumeHoldEnd"
          @pointercancel="onVolumeHoldEnd" @pointerleave="onVolumeHoldEnd" v-press
          class="volume-btn button-interactive-subtle">
          <SvgIcon :name="icon" :size="32" />
        </button>
      </div>

      <!-- App Container -->
      <div class="app-container">
        <!-- Mobile: first 3 dock apps (audio mix + features) -->
        <button v-for="({ id, icon }, index) in dockApps" :key="`mobile-${id}`"
          :ref="el => { if (el) mobileDockItems[index] = el }" @click="() => handleAppClick(id, index)"
          :disabled="unifiedStore.systemState.transitioning" :style="{ transitionDelay: getDockItemDelay(index) }"
          v-press class="dock-item button-interactive-subtle mobile-only">
          <AppIcon :name="icon" size="large" class="dock-item-icon" />
        </button>

        <!-- Desktop: Audio Sources -->
        <button v-for="({ id, icon }, index) in enabledAudioSources" :key="`desktop-audio-${id}`"
          :ref="el => { if (el) desktopDockItems[index] = el }" @click="() => handleAppClick(id, index)"
          :disabled="unifiedStore.systemState.transitioning" :style="{ transitionDelay: getDockItemDelay(index) }"
          v-press class="dock-item button-interactive-subtle desktop-only">
          <AppIcon :name="icon" size="large" class="dock-item-icon" />
        </button>

        <!-- Separator - Desktop: shown if features exist, Mobile: shown if toggle button exists -->
        <div v-if="enabledFeatures.length > 0 || additionalDockApps.length > 0"
          :style="{ transitionDelay: getDockItemDelay(enabledAudioSources.length) }"
          class="dock-separator">
        </div>

        <!-- Mobile: Toggle Additional Apps (if more than 3 apps) -->
        <button v-if="additionalDockApps.length > 0" @click="handleToggleClick"
          :style="{ transitionDelay: getDockItemDelay(dockApps.length) }" v-press
          class="dock-item toggle-btn mobile-only button-interactive">
          <SvgIcon :name="showAdditionalApps ? 'closeDots' : 'threeDots'" :size="32" class="toggle-icon" />
        </button>

        <!-- Desktop: Features -->
        <button v-for="({ id, icon, handler }, index) in enabledFeatures" :key="`desktop-feature-${id}`"
          @click="handler" :style="{ transitionDelay: getDockItemDelay(enabledAudioSources.length + 1 + index) }"
          v-press class="dock-item desktop-only button-interactive-subtle">
          <AppIcon :name="icon" size="large" class="dock-item-icon" />
        </button>
      </div>

      <!-- Active item indicator -->
      <div ref="activeIndicator" class="active-indicator" :style="indicatorStyle"></div>
    </div>
  </nav>
</template>

<script setup>
import { ref, computed, onMounted, watch, nextTick, inject } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useTimer } from '@/composables/useTimer';
import { useSettingsStore } from '@/stores/settingsStore';
import { useI18n } from '@/services/i18n';
import { useIsMobile } from '@/composables/useIsMobile';
import { useDockDrag } from '@/composables/useDockDrag';
import { useVolumeHold } from '@/composables/useVolumeHold';
import AppIcon from '@/components/ui/AppIcon.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import { ALL_AUDIO_SOURCES } from '@/constants/audioSources';

const { t } = useI18n();
const settingsStore = useSettingsStore();
const registerDockControl = inject('registerDockControl', null);

// === ANIMATION TIMING ===
const DOCK_ANIM_INITIAL_DELAY = 0.08;  // Initial delay in seconds
const DOCK_ANIM_STAGGER = 0.015;        // Stagger between items in seconds


// Actions with reactive titles
const ALL_ADDITIONAL_ACTIONS = computed(() => [
  { id: 'equalizer', icon: 'equalizer', title: t('equalizer.title'), handler: () => emit('open-equalizer') },
  { id: 'multiroom', icon: 'multiroom', title: t('audioSources.multiroom'), handler: () => emit('open-multiroom') },
  { id: 'settings', icon: 'settings', title: t('common.settings'), handler: () => emit('open-settings') }
]);

// === DYNAMIC CONFIGURATION ===
const enabledApps = computed(() => settingsStore.buildEnabledAppsArray());

const enabledAudioSources = computed(() => {
  return enabledApps.value
    .filter(source => ALL_AUDIO_SOURCES.includes(source))
    .map(source => ({ id: source, icon: source }));
});

const enabledFeatures = computed(() => {
  return ALL_ADDITIONAL_ACTIONS.value
    .filter(action => enabledApps.value.includes(action.id));
});

// All enabled apps in order (for mobile)
const allEnabledApps = computed(() => {
  return [...enabledAudioSources.value, ...enabledFeatures.value];
});

// Split into two groups: if <=4 apps, all in dock; otherwise first 3 in dock and the rest in "additional" (for mobile)
const dockApps = computed(() => {
  const total = allEnabledApps.value.length;
  return total <= 4 ? allEnabledApps.value : allEnabledApps.value.slice(0, 3);
});
const additionalDockApps = computed(() => {
  const total = allEnabledApps.value.length;
  return total <= 4 ? [] : allEnabledApps.value.slice(3);
});

// === STORE AND CONTROLS ===
const unifiedStore = useUnifiedAudioStore();

const volumeControlsWithSteps = computed(() => {
  const step = unifiedStore.volumeState.step_mobile_db;
  return [
    { icon: 'minus', delta: -step },
    { icon: 'plus', delta: step }
  ];
});

// === EMISSIONS ===
const emit = defineEmits(['open-equalizer', 'open-multiroom', 'open-settings']);

// === TEMPLATE REFS ===
const dragZone = ref(null);
const dockContainer = ref(null);
const dock = ref(null);
const activeIndicator = ref(null);
const additionalAppsContainer = ref(null);
const mobileDockItems = ref([]);
const desktopDockItems = ref([]);

// === VISIBILITY STATE ===
const isVisible = ref(false);
const isFullyVisible = ref(false);
const showAdditionalApps = ref(false);
const showDragIndicator = ref(false);
const additionalAppsInDOM = ref(false);

// === TIMERS ===
const timer = useTimer();
let hideTimeout = null;
let additionalHideTimeout = null;

const { isMobile } = useIsMobile();
const isDesktop = () => !isMobile.value;

const getDockItemDelay = (index) => `${DOCK_ANIM_INITIAL_DELAY + index * DOCK_ANIM_STAGGER}s`;

// === DOCK SHOW/HIDE ===
const startHideTimer = () => {
  timer.clear(hideTimeout);
  if (unifiedStore.systemState.active_source === 'none') return;
  hideTimeout = timer.setTimeout(hideDock, 10000);
};

const resetHideTimer = () => isVisible.value && startHideTimer();

const showDock = () => {
  if (isVisible.value) return;
  isVisible.value = true;
  isFullyVisible.value = false;

  // Reset items: disable transition, force reflow, re-enable
  dockContainer.value.classList.add('resetting');
  void dockContainer.value.offsetHeight;
  dockContainer.value.classList.remove('resetting');

  // Wait one frame so the reset state is painted before triggering visible transition
  requestAnimationFrame(() => {
    startHideTimer();
    timer.setTimeout(() => isFullyVisible.value = true, 400);
    timer.setTimeout(updateActiveIndicator, 500);
  });
};

const hideDock = () => {
  if (!isVisible.value) return;
  isFullyVisible.value = false;
  showAdditionalApps.value = false;
  isVisible.value = false;
  timer.clear(hideTimeout);
  timer.clear(additionalHideTimeout);
  indicatorStyle.value.opacity = '0';
  timer.setTimeout(() => additionalAppsInDOM.value = false, 400);

  volumeHold.onVolumeHoldEnd();
  drag.resetGestureState();
};

// === DRAG COMPOSABLE ===
const drag = useDockDrag({
  dragZone,
  dock,
  dockContainer,
  additionalAppsContainer,
  isVisible,
  showAdditionalApps,
  onShow: showDock,
  onHide: hideDock,
  onCloseAdditionalApps: () => closeAdditionalApps(),
  onVolumeHoldEnd: (e) => volumeHold.onVolumeHoldEnd(e),
  onResetHideTimer: resetHideTimer,
});

const { isDragging } = drag;

// === VOLUME HOLD COMPOSABLE ===
const volumeHold = useVolumeHold({
  adjustVolume: (delta) => unifiedStore.adjustVolume(delta),
  onHoldStart: (delta, intervalMs) => unifiedStore.startVolumeInterpolation(delta, intervalMs),
  onHoldEnd: () => unifiedStore.stopVolumeInterpolation(),
  gestureHasMoved: drag.gestureHasMoved,
  gestureStartPosition: drag.gestureStartPosition,
  getEventX: drag.getEventX,
  getEventY: drag.getEventY,
});

const { onVolumeHoldStart, onVolumeHoldEnd } = volumeHold;

// === ACTIVE INDICATOR ===
const indicatorStyle = ref({
  opacity: '0',
  transform: 'translateX(0px)',
});

const activeSourceIndex = computed(() => {
  if (isDesktop()) {
    return enabledAudioSources.value.findIndex(app => app.id === unifiedStore.systemState.active_source);
  } else {
    const currentSource = unifiedStore.systemState.active_source;
    if (!ALL_AUDIO_SOURCES.includes(currentSource)) return -1;
    return dockApps.value.findIndex(app => app.id === currentSource);
  }
});

const getDockItems = () => isDesktop() ? desktopDockItems.value : mobileDockItems.value;

// Compute translateX offset corrected for CSS transform scale on #app
const getIndicatorOffset = (targetItem) => {
  if (!targetItem || !dock.value) return null;
  const dockRect = dock.value.getBoundingClientRect();
  const itemRect = targetItem.getBoundingClientRect();
  const w = dock.value.offsetWidth;
  const scale = w ? dockRect.width / w : 1;
  return (itemRect.left - dockRect.left + (itemRect.width / 2) - 2) / scale;
};

const updateActiveIndicator = () => {
  if (!isVisible.value || activeSourceIndex.value === -1) {
    indicatorStyle.value.opacity = '0';
    return;
  }

  nextTick(() => {
    const items = getDockItems();
    const targetItem = items[activeSourceIndex.value];
    const offsetX = getIndicatorOffset(targetItem);
    if (offsetX === null) return;

    indicatorStyle.value = { opacity: '0', transform: `translateX(${offsetX}px)`, transition: 'none' };

    timer.setTimeout(() => {
      indicatorStyle.value = {
        opacity: '1',
        transform: `translateX(${offsetX}px)`,
      };
    }, 50);
  });
};

const moveIndicatorTo = (index) => {
  if (!isVisible.value) return;
  nextTick(() => {
    const items = getDockItems();
    const targetItem = items[index];
    const offsetX = getIndicatorOffset(targetItem);
    if (offsetX === null) return;
    indicatorStyle.value = {
      opacity: '1',
      transform: `translateX(${offsetX}px)`,
    };
  });
};

// === CLICK HANDLERS ===
const onDragZoneClick = () => {
  if (!isDesktop() && !isDragging.value && !isVisible.value) {
    showDock();
  }
};

const onIndicatorClick = () => {
  if (!isDragging.value && !isVisible.value) {
    showDock();
  }
};

const handleAppClick = (appId, index) => {
  resetHideTimer();

  const isAudioSource = ALL_AUDIO_SOURCES.includes(appId);
  if (isAudioSource) {
    moveIndicatorTo(index);
    unifiedStore.changeSource(appId);
  } else {
    const action = ALL_ADDITIONAL_ACTIONS.value.find(a => a.id === appId);
    if (action && action.handler) {
      action.handler();
    }
  }
};

const handleAdditionalAppClick = (appId) => {
  if (drag.additionalDragMoved) {
    drag.resetAdditionalDragMoved();
    return;
  }

  resetHideTimer();

  const isAudioSource = ALL_AUDIO_SOURCES.includes(appId);
  if (isAudioSource) {
    unifiedStore.changeSource(appId);
  } else {
    const action = ALL_ADDITIONAL_ACTIONS.value.find(a => a.id === appId);
    if (action && action.handler) {
      action.handler();
    }
  }

  closeAdditionalApps();
};

const getAppTitle = (appId) => {
  const audioSourceTitles = {
    'spotify': t('audioSources.spotify'),
    'bluetooth': t('audioSources.bluetooth'),
    'mac': t('audioSources.macOS'),
    'radio': t('audioSources.radio'),
    'podcast': t('audioSources.podcasts'),
    'airplay': t('audioSources.airplay'),
    'cd': t('audioSources.cd')
  };

  if (ALL_AUDIO_SOURCES.includes(appId)) {
    return audioSourceTitles[appId] || appId;
  }

  const action = ALL_ADDITIONAL_ACTIONS.value.find(a => a.id === appId);
  return action?.title || appId;
};

// === ADDITIONAL APPS MANAGEMENT ===
const toggleAdditionalApps = () => {
  if (!showAdditionalApps.value) {
    additionalAppsInDOM.value = true;
    timer.clear(additionalHideTimeout);
    nextTick(() => {
      requestAnimationFrame(() => {
        showAdditionalApps.value = true;
        drag.setupAdditionalDragEvents();
      });
    });
  } else {
    closeAdditionalApps();
  }
};

const closeAdditionalApps = () => {
  if (!showAdditionalApps.value) return;
  showAdditionalApps.value = false;
  timer.clear(additionalHideTimeout);
  additionalHideTimeout = timer.setTimeout(() => additionalAppsInDOM.value = false, 1200);
};

const handleToggleClick = (event) => {
  if (event.target.closest('.toggle-icon')) event.stopPropagation();
  resetHideTimer();
  toggleAdditionalApps();
};

// === LIFECYCLE ===
watch(() => unifiedStore.systemState.active_source, (newSource) => {
  if (newSource === 'none') {
    indicatorStyle.value.opacity = '0';
    timer.clear(hideTimeout);
  } else if (isVisible.value && indicatorStyle.value.opacity === '1') {
    // Indicator already visible — slide to new position
    const newIndex = activeSourceIndex.value;
    if (newIndex !== -1) {
      moveIndicatorTo(newIndex);
    } else {
      indicatorStyle.value.opacity = '0';
    }
    startHideTimer();
  } else {
    // Dock just appeared or indicator hidden — fade in at position
    updateActiveIndicator();
    if (isVisible.value) {
      startHideTimer();
    }
  }
});

onMounted(() => {
  drag.setupDragEvents();

  if (registerDockControl) {
    registerDockControl(showDock);
  }

  timer.setTimeout(() => showDragIndicator.value = true, 800);
});
// hideTimeout / additionalHideTimeout are auto-cleared on unmount by useTimer;
// the drag and volumeHold composables register their own onUnmounted hooks internally.
</script>

<style scoped>
.drag-zone {
  position: fixed;
  width: 280px;
  bottom: calc(0px + env(safe-area-inset-bottom, 0px));
  left: 50%;
  transform: translateX(-50%);
  height: 12%;
  opacity: 0.2;
  z-index: 3999;
  cursor: grab;
  user-select: none;
}

.drag-zone.dragging {
  cursor: grabbing;
  height: 50%;
}

.additional-apps-container {
  --glass-bg: var(--color-background-medium-16);
  --glass-blur: var(--blur-03);
  --glass-radius: var(--radius-07);
  --glass-stroke-width: 1.5px;
  position: absolute;
  bottom: 100%;
  left: 0;
  right: 0;
  transform: translateY(calc(-1 * var(--space-03) + var(--space-06)));
  z-index: 3998;
  border-radius: var(--radius-07);
  padding: var(--space-04);
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--transition-spring-fast), transform var(--transition-spring-fast);
  cursor: grab;
}

.additional-apps-container.visible {
  opacity: 1;
  transform: translateY(calc(-1 * var(--space-03)));
  pointer-events: auto;
}

.additional-app-content {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-02);
  width: 100%;
  background: var(--color-background-neutral-50);
  border: none;
  cursor: pointer;
  border-radius: var(--radius-04);
  transition: opacity var(--transition-spring-fast), transform var(--transition-spring-fast);
  opacity: 0;
  transform: translateY(20px) scale(0.95);
}

.additional-apps-container.visible .additional-app-content {
  opacity: 1;
  transform: translateY(0) scale(1);
  transition-delay: var(--stagger, 0s);
}


.app-title {
  color: var(--color-text);
}

.dock-container {
  position: fixed;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%) translateY(164px) scale(0.9);
  z-index: 4000;
  transition: transform var(--transition-spring-light);
  width: fit-content;
  will-change: transform;
  -webkit-backface-visibility: hidden;
  backface-visibility: hidden;
}

.dock-container.visible {
  transform: translateX(-50%) translateY(calc(-29px - env(safe-area-inset-bottom, 0px))) scale(1);
}

.dock {
  --glass-bg: var(--color-background-medium-16);
  --glass-blur: var(--blur-03);
  --glass-radius: var(--radius-07);
  --glass-stroke-width: 1.5px;
  position: relative;
  border-radius: var(--radius-07);
  padding: var(--space-04);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-03);
  z-index: 0;
  overflow: hidden;
}

.additional-app-content {
  --glass-radius: var(--radius-04);
  --glass-stroke-width: 1.5px;
}

.volume-controls {
  display: flex;
  gap: var(--space-02);
  width: 100%;
  opacity: 0;
  transform: translateY(20px) scale(0.8) translateZ(0);
  transition: opacity var(--transition-spring), transform var(--transition-spring);
  will-change: transform, opacity;
}

.volume-btn {
  display: flex;
  align-content: center;
  justify-content: center;
  flex: 1;
  background: var(--color-background-neutral-50);
  border-radius: var(--radius-04);
  cursor: pointer;
  color: var(--color-text-secondary);
  padding: var(--space-02);
  transition: opacity var(--transition-spring), transform var(--transition-spring);
  user-select: none;
  -webkit-user-select: none;
  -webkit-touch-callout: none;
  touch-action: manipulation;
}

.app-container {
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

.dock-separator {
  width: 2px;
  height: var(--space-07);
  background: var(--color-background-neutral-50);
  border-radius: var(--radius-full);
  opacity: 0;
  transform: translateY(20px) scale(0.8) translateZ(0);
  transition: opacity var(--transition-spring), transform var(--transition-spring);
  will-change: transform, opacity;
}

.dock-item {
  cursor: pointer;
  background: none;
  border: none;
  opacity: 0;
  transform: translateY(20px) scale(0.8) translateZ(0);
  transition: opacity var(--transition-spring), transform var(--transition-spring);
  will-change: transform, opacity;
}

.dock-container.visible .dock-item,
.dock-container.visible .dock-separator,
.dock-container.visible .volume-controls {
  opacity: 1;
  transform: translateY(0) scale(1) translateZ(0);
}

.dock-container.visible.fully-visible .dock-item,
.dock-container.visible.fully-visible .dock-separator,
.dock-container.visible.fully-visible .volume-controls {
  transition-delay: 0s !important;
}

.dock-container.resetting .dock-item,
.dock-container.resetting .dock-separator,
.dock-container.resetting .volume-controls {
  transition: none !important;
}

.toggle-btn {
  width: 54px;
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-background-neutral-50);
  border-radius: var(--radius-04);
  padding: 0;
  color: var(--color-text-secondary);
}


.dock-item-icon {
  width: 48px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.active-indicator {
  position: absolute;
  bottom: 8px;
  left: 0;
  width: 6px;
  height: 4px;
  background: var(--color-background-contrast);
  border-radius: var(--radius-full);
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--transition-normal), transform var(--transition-spring-light);
}

.dock-item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.dock-indicator {
  position: fixed;
  bottom: calc(var(--space-03) - 2px + env(safe-area-inset-bottom, 0px));
  left: 50%;
  transform: translateX(-50%);
  width: 96px;
  height: var(--space-01);
  background: var(--color-background-medium-16);
  border-radius: var(--radius-full);
  z-index: 998;
  opacity: 0;
  pointer-events: none;
  cursor: pointer;
  transition: opacity var(--transition-normal), transform var(--transition-spring);
}

.dock-indicator.visible {
  opacity: 1;
  pointer-events: auto;
}

.dock-indicator.hidden {
  opacity: 0;
  transform: translateX(-50%) translateY(-20px);
  pointer-events: none;
}

.mobile-only {
  display: flex;
}

.desktop-only {
  display: flex;
}

@media (max-aspect-ratio: 4/3) {
  .drag-zone {
    height: 5%;
  }

  .desktop-only {
    display: none;
  }

  .app-container {
    gap: var(--space-02);
  }

  .dock-indicator {
    width: 64px;
  }
}

@media not (max-aspect-ratio: 4/3) {
  .mobile-only {
    display: none;
  }

  .additional-apps-container {
    display: none !important;
  }

  .dock {
    flex-direction: row;
  }
}
</style>
