<!-- frontend/src/components/navigation/BottomNavigation.vue -->
<template>
  <!-- Zone de drag invisible -->
  <div ref="dragZone" class="drag-zone" :class="{ dragging: isDragging }" @click.stop="onDragZoneClick"></div>

  <!-- Indicateur de drag -->
  <div class="dock-indicator" :class="{ hidden: isVisible, visible: showDragIndicator }" @click.stop="onIndicatorClick">
  </div>

  <!-- Dock de navigation -->
  <nav ref="dockContainer" class="dock-container" :class="{ visible: isVisible, 'fully-visible': isFullyVisible }">
    <!-- Additional Apps - Mobile uniquement -->
    <div v-if="additionalAppsInDOM && additionalDockApps.length > 0" ref="additionalAppsContainer"
      class="additional-apps-container mobile-only" :class="{ visible: showAdditionalApps }">

      <button v-for="(app, index) in additionalDockApps.slice().reverse()" :key="app.id"
        @click="() => handleAdditionalAppClick(app.id)" @touchstart.passive="addPressEffect" @mousedown="addPressEffect"
        class="additional-app-content button-interactive-subtle">
        <AppIcon :name="app.icon" :size="32" />
        <div class="app-title heading-2">{{ getAppTitle(app.id) }}</div>
      </button>
    </div>

    <div ref="dock" class="dock">
      <!-- Volume Controls - Mobile uniquement -->
      <div class="volume-controls mobile-only">
        <button v-for="{ icon, handler, delta } in volumeControlsWithSteps" :key="icon"
          @pointerdown="(e) => onVolumeHoldStart(delta, e)" @pointerup="onVolumeHoldEnd"
          @pointercancel="onVolumeHoldEnd" @pointerleave="onVolumeHoldEnd" class="volume-btn button-interactive-subtle">
          <Icon :name="icon" :size="32" />
        </button>
      </div>

      <!-- App Container -->
      <div class="app-container">
        <!-- Mobile: 3 premières apps du dock (mix audio + features) -->
        <button
          v-for="({ id, icon }, index) in dockApps"
          :key="`mobile-${id}`"
          :ref="el => { if (el) mobileDockItems[index] = el }"
          @click="() => handleAppClick(id, index)"
          @touchstart.passive="addPressEffect"
          @mousedown="addPressEffect"
          :disabled="unifiedStore.systemState.transitioning"
          :style="{ transitionDelay: `${0.1 + index * 0.05}s` }"
          class="dock-item button-interactive-subtle mobile-only">
          <AppIcon :name="icon" size="large" class="dock-item-icon" />
        </button>

        <!-- Desktop: Audio Plugins -->
        <button
          v-for="({ id, icon }, index) in enabledAudioPlugins"
          :key="`desktop-audio-${id}`"
          :ref="el => { if (el) desktopDockItems[index] = el }"
          @click="() => handleAppClick(id, index)"
          @touchstart.passive="addPressEffect"
          @mousedown="addPressEffect"
          :disabled="unifiedStore.systemState.transitioning"
          :style="{ transitionDelay: `${0.1 + index * 0.05}s` }"
          class="dock-item button-interactive-subtle desktop-only">
          <AppIcon :name="icon" size="large" class="dock-item-icon" />
        </button>

        <!-- Séparateur - Desktop uniquement, toujours affiché si on a des features -->
        <div
          v-if="enabledFeatures.length > 0"
          :style="{ transitionDelay: `${0.1 + enabledAudioPlugins.length * 0.05}s` }"
          class="dock-separator desktop-only">
        </div>

        <!-- Mobile: Toggle Additional Apps (si plus de 3 apps) -->
        <button
          v-if="additionalDockApps.length > 0"
          @click="handleToggleClick"
          @touchstart.passive="addPressEffect"
          @mousedown="addPressEffect"
          :style="{ transitionDelay: `${0.1 + dockApps.length * 0.05}s` }"
          class="dock-item toggle-btn mobile-only button-interactive">
          <Icon :name="showAdditionalApps ? 'closeDots' : 'threeDots'" :size="32" class="toggle-icon" />
        </button>

        <!-- Desktop: Features -->
        <button
          v-for="({ id, icon, handler }, index) in enabledFeatures"
          :key="`desktop-feature-${id}`"
          @click="handler"
          @touchstart.passive="addPressEffect"
          @mousedown="addPressEffect"
          :style="{ transitionDelay: `${0.1 + (enabledAudioPlugins.length + 1 + index) * 0.05}s` }"
          class="dock-item desktop-only button-interactive-subtle">
          <AppIcon :name="icon" size="large" class="dock-item-icon" />
        </button>
      </div>

      <!-- Indicateur d'élément actif -->
      <div ref="activeIndicator" class="active-indicator" :style="indicatorStyle"></div>
    </div>
  </nav>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick, getCurrentInstance } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import AppIcon from '@/components/ui/AppIcon.vue';
import Icon from '@/components/ui/Icon.vue';

const { t } = useI18n();
const instance = getCurrentInstance();
const $t = instance.appContext.config.globalProperties.$t;
const { on } = useWebSocket();

// === CONFIGURATION STATIQUE ===
const ALL_AUDIO_SOURCES = ['librespot', 'bluetooth', 'roc', 'radio'];


// Actions avec titres réactifs
const ALL_ADDITIONAL_ACTIONS = computed(() => [
  { id: 'multiroom', icon: 'multiroom', title: t('multiroom.title'), handler: () => emit('open-snapcast') },
  { id: 'equalizer', icon: 'equalizer', title: t('equalizer.title'), handler: () => emit('open-equalizer') },
  { id: 'settings', icon: 'settings', title: t('common.settings'), handler: () => emit('open-settings') }
]);

// === CONFIGURATION DYNAMIQUE ===
const enabledApps = ref(["librespot", "bluetooth", "roc", "radio", "multiroom", "equalizer", "settings"]);
const mobileVolumeSteps = ref(5);

// Computed pour séparer audio plugins et features
const enabledAudioPlugins = computed(() => {
  return ALL_AUDIO_SOURCES
    .filter(source => enabledApps.value.includes(source))
    .map(source => ({ id: source, icon: source }));
});

const enabledFeatures = computed(() => {
  return ALL_ADDITIONAL_ACTIONS.value
    .filter(action => enabledApps.value.includes(action.id));
});

// Toutes les apps activées dans l'ordre (pour mobile)
const allEnabledApps = computed(() => {
  return [...enabledAudioPlugins.value, ...enabledFeatures.value];
});

// Diviser en deux groupes : si ≤4 apps, toutes dans le dock, sinon 3 premières dans le dock et le reste dans additional (pour mobile)
const dockApps = computed(() => {
  const total = allEnabledApps.value.length;
  return total <= 4 ? allEnabledApps.value : allEnabledApps.value.slice(0, 3);
});
const additionalDockApps = computed(() => {
  const total = allEnabledApps.value.length;
  return total <= 4 ? [] : allEnabledApps.value.slice(3);
});

// === STORE ET CONTRÔLES ===
const unifiedStore = useUnifiedAudioStore();

const volumeControlsWithSteps = computed(() => [
  { icon: 'minus', handler: () => unifiedStore.adjustVolume(-mobileVolumeSteps.value), delta: -mobileVolumeSteps.value },
  { icon: 'plus', handler: () => unifiedStore.adjustVolume(mobileVolumeSteps.value), delta: mobileVolumeSteps.value }
]);

// === ÉMISSIONS ===
const emit = defineEmits(['open-snapcast', 'open-equalizer', 'open-settings']);

// === REFS PRINCIPAUX ===
const dragZone = ref(null);
const dockContainer = ref(null);
const dock = ref(null);
const activeIndicator = ref(null);
const additionalAppsContainer = ref(null);
const mobileDockItems = ref([]);
const desktopDockItems = ref([]);

// === ÉTAT GLOBAL ===
const isVisible = ref(false);
const isFullyVisible = ref(false);
const showAdditionalApps = ref(false);
const showDragIndicator = ref(false);
const additionalAppsInDOM = ref(false);

// === GESTION DU DRAG ===
const isDragging = ref(false);
const gestureHasMoved = ref(false);
const gestureStartPosition = ref({ x: 0, y: 0 });
const MOVE_THRESHOLD = 10;

// Variables de drag
let dragStartY = 0, dragCurrentY = 0, dragStartTime = 0;
let dragActionTaken = ref(false);
let isDraggingAdditional = false, additionalDragStartY = 0, additionalDragMoved = false;

// === GESTION VOLUME HOLD ===
const volumeStartTimer = ref(null);
const volumeRepeatTimer = ref(null);
const isVolumeHolding = ref(false);
const currentVolumeDelta = ref(0);
const volumeActionTaken = ref(false);
const volumePointerType = ref(null);

// === TIMERS ===
let hideTimeout = null, additionalHideTimeout = null, clickTimeout = null, dragGraceTimeout = null;

// === COMPUTED ===
const activeSourceIndex = computed(() => {
  // Sur desktop, chercher dans les audio plugins uniquement
  // Sur mobile, chercher uniquement dans les sources audio visibles dans dockApps
  if (isDesktop()) {
    return enabledAudioPlugins.value.findIndex(app => app.id === unifiedStore.systemState.active_source);
  } else {
    // En mobile: trouver l'index de la source active parmi TOUTES les apps du dock
    // Mais seulement si c'est une source audio
    const currentSource = unifiedStore.systemState.active_source;
    if (!ALL_AUDIO_SOURCES.includes(currentSource)) {
      return -1; // Pas une source audio, pas d'indicateur
    }
    return dockApps.value.findIndex(app => app.id === currentSource);
  }
});

const indicatorStyle = ref({
  opacity: '0',
  transform: 'translateX(0px)',
  transition: 'all var(--transition-spring)'
});

// === UTILITAIRES ===
const getEventY = (e) => e.type.includes('touch') || e.pointerType === 'touch'
  ? (e.touches?.[0]?.clientY || e.changedTouches?.[0]?.clientY || e.clientY) : e.clientY;

const getEventX = (e) => e.type.includes('touch') || e.pointerType === 'touch'
  ? (e.touches?.[0]?.clientX || e.changedTouches?.[0]?.clientX || e.clientX) : e.clientX;

const isDesktop = () => window.matchMedia('not (max-aspect-ratio: 4/3)').matches;

const clearAllTimers = () => {
  [hideTimeout, additionalHideTimeout, clickTimeout, volumeStartTimer.value, volumeRepeatTimer.value, dragGraceTimeout]
    .forEach(timer => timer && clearTimeout(timer));
  volumeStartTimer.value = volumeRepeatTimer.value = dragGraceTimeout = null;
  dragActionTaken.value = volumeActionTaken.value = false;
};

const startHideTimer = () => {
  clearTimeout(hideTimeout);
  hideTimeout = setTimeout(hideDock, 10000);
};

const resetHideTimer = () => isVisible.value && startHideTimer();

const resetGestureState = () => {
  gestureHasMoved.value = false;
  gestureStartPosition.value = { x: 0, y: 0 };
};

// === GESTION VOLUME HOLD ===
const onVolumeHoldStart = (delta, event) => {
  if (volumePointerType.value && volumePointerType.value !== event.pointerType) {
    return;
  }

  volumePointerType.value = event.pointerType;
  gestureStartPosition.value = { x: getEventX(event), y: getEventY(event) };
  gestureHasMoved.value = false;
  currentVolumeDelta.value = delta;
  volumeActionTaken.value = false;

  addPressEffect(event);

  volumeStartTimer.value = setTimeout(() => {
    if (!gestureHasMoved.value && !volumeActionTaken.value && volumePointerType.value === event.pointerType) {
      volumeActionTaken.value = true;
      unifiedStore.adjustVolume(delta);
      isVolumeHolding.value = true;

      volumeRepeatTimer.value = setTimeout(() => {
        if (isVolumeHolding.value) {
          const repeatInterval = setInterval(() => {
            if (isVolumeHolding.value) {
              unifiedStore.adjustVolume(currentVolumeDelta.value);
            } else {
              clearInterval(repeatInterval);
            }
          }, 50);
          volumeRepeatTimer.value = repeatInterval;
        }
      }, 300);
    }
  }, 200);

  resetHideTimer();
};

const onVolumeHoldEnd = (event) => {
  if (event && volumePointerType.value && event.pointerType !== volumePointerType.value) {
    return;
  }

  if (!volumeActionTaken.value && !gestureHasMoved.value && currentVolumeDelta.value !== 0) {
    volumeActionTaken.value = true;
    unifiedStore.adjustVolume(currentVolumeDelta.value);
  }

  isVolumeHolding.value = false;
  volumePointerType.value = null;

  if (volumeStartTimer.value) {
    clearTimeout(volumeStartTimer.value);
    volumeStartTimer.value = null;
  }

  if (volumeRepeatTimer.value) {
    clearTimeout(volumeRepeatTimer.value);
    clearInterval(volumeRepeatTimer.value);
    volumeRepeatTimer.value = null;
  }

  currentVolumeDelta.value = 0;
  volumeActionTaken.value = false;
};

// === GESTION DOCK ===
const showDock = () => {
  if (isVisible.value) return;
  isVisible.value = true;
  isFullyVisible.value = false;
  startHideTimer();
  setTimeout(() => isFullyVisible.value = true, 400);
  setTimeout(updateActiveIndicator, 500);
};

const hideDock = () => {
  if (!isVisible.value) return;
  isFullyVisible.value = false;
  showAdditionalApps.value = false;
  isVisible.value = false;
  clearAllTimers();
  indicatorStyle.value.opacity = '0';
  setTimeout(() => additionalAppsInDOM.value = false, 400);

  onVolumeHoldEnd();
  resetGestureState();
};

// === GESTION DRAG AMÉLIORÉE ===
const onDragStart = (e) => {
  isDragging.value = true;
  dragStartY = getEventY(e);
  dragCurrentY = dragStartY;
  dragStartTime = Date.now();
  dragActionTaken.value = false;

  if (dragGraceTimeout) {
    clearTimeout(dragGraceTimeout);
    dragGraceTimeout = null;
  }

  resetGestureState();
};

const onDragMove = (e) => {
  if (isDraggingAdditional) {
    const deltaY = getEventY(e) - additionalDragStartY;
    // Marquer qu'un mouvement a été détecté
    if (Math.abs(deltaY) > 5) {
      additionalDragMoved = true;
      e.preventDefault();
    }
    // Détecter mouvement significatif pour fermer
    if (Math.abs(deltaY) >= 20 && deltaY > 0) {
      e.preventDefault(); // Empêcher le scroll
      closeAdditionalApps();
      isDraggingAdditional = false;
      additionalDragMoved = false;
    }
    return;
  }

  if (!isDragging.value) return;

  const currentY = getEventY(e);
  const currentX = getEventX(e);

  // Vérifier mouvement pour annuler volume hold
  if (!gestureHasMoved.value) {
    const deltaX = Math.abs(currentX - gestureStartPosition.value.x);
    const deltaY = Math.abs(currentY - gestureStartPosition.value.y);

    if (deltaX > MOVE_THRESHOLD || deltaY > MOVE_THRESHOLD) {
      gestureHasMoved.value = true;
      onVolumeHoldEnd();
    }
  }

  // Logique de drag améliorée
  dragCurrentY = currentY;
  const deltaY = dragStartY - dragCurrentY;
  const dragDuration = Date.now() - dragStartTime;
  const velocity = Math.abs(deltaY) / Math.max(dragDuration, 1);

  // Seuil adaptatif selon la vitesse
  let threshold = 30;
  if (velocity >= 0.5) {
    threshold = Math.max(20, 30 - (velocity * 10));
  }

  if (Math.abs(deltaY) >= threshold && !dragActionTaken.value) {
    dragActionTaken.value = true;

    if (deltaY > 0 && !isVisible.value) {
      showDock();
    } else if (deltaY < 0 && isVisible.value) {
      hideDock();
    }

    dragGraceTimeout = setTimeout(() => {
      isDragging.value = false;
      dragActionTaken.value = false;
      resetGestureState();
    }, 200);
  }
};

const onDragEnd = () => {
  clearTimeout(clickTimeout);
  if (isDraggingAdditional) {
    isDraggingAdditional = false;
    additionalDragMoved = false;
    return;
  }

  if (!dragActionTaken.value) {
    isDragging.value = false;
    resetGestureState();
  }

  resetHideTimer();
};

// === GESTION CLICS ===
const onClickOutside = (event) => {
  if (!isVisible.value ||
    (dockContainer.value && dockContainer.value.contains(event.target)) ||
    event.target.closest('.modal-overlay, .modal-container, .modal-content')) {
    return;
  }
  hideDock();
};

const onDragZoneClick = () => {
  if (!isDesktop() && !isDragging.value && !isVisible.value) {
    showDock();
  }
};

const onIndicatorClick = () => {
  if (!isDesktop() && !isDragging.value && !isVisible.value) {
    showDock();
  }
};

// === ACTIONS ===
const handleAppClick = (appId, index) => {
  resetHideTimer();

  // Si c'est une source audio, changer la source
  const isAudioSource = ALL_AUDIO_SOURCES.includes(appId);
  if (isAudioSource) {
    moveIndicatorTo(index);
    unifiedStore.changeSource(appId);
  } else {
    // Sinon, exécuter le handler de l'action
    const action = ALL_ADDITIONAL_ACTIONS.value.find(a => a.id === appId);
    if (action && action.handler) {
      action.handler();
    }
  }
};

const handleAdditionalAppClick = (appId) => {
  // Ignorer le clic si un drag vient d'avoir lieu
  if (additionalDragMoved) {
    additionalDragMoved = false;
    return;
  }

  resetHideTimer();

  // Si c'est une source audio, changer la source
  const isAudioSource = ALL_AUDIO_SOURCES.includes(appId);
  if (isAudioSource) {
    unifiedStore.changeSource(appId);
  } else {
    // Sinon, exécuter le handler de l'action
    const action = ALL_ADDITIONAL_ACTIONS.value.find(a => a.id === appId);
    if (action && action.handler) {
      action.handler();
    }
  }
};

const getAppTitle = (appId) => {
  // Traductions pour les sources audio
  const audioSourceTitles = {
    'librespot': t('applications.spotify'),
    'bluetooth': t('applications.bluetooth'),
    'roc': t('applications.macOS'),
    'radio': t('audioSources.radio')
  };

  // Si c'est une source audio, retourner la traduction
  if (ALL_AUDIO_SOURCES.includes(appId)) {
    return audioSourceTitles[appId] || appId;
  }

  // Sinon, chercher dans les actions
  const action = ALL_ADDITIONAL_ACTIONS.value.find(a => a.id === appId);
  return action?.title || appId;
};

// === INDICATEUR ACTIF ===
const getDockItems = () => isDesktop() ? desktopDockItems.value : mobileDockItems.value;

const updateActiveIndicator = () => {
  if (!isVisible.value || activeSourceIndex.value === -1) {
    indicatorStyle.value.opacity = '0';
    return;
  }

  nextTick(() => {
    const items = getDockItems();
    const targetItem = items[activeSourceIndex.value];
    if (!targetItem || !dock.value) return;

    const dockRect = dock.value.getBoundingClientRect();
    const itemRect = targetItem.getBoundingClientRect();
    const offsetX = itemRect.left - dockRect.left + (itemRect.width / 2) - 2;

    indicatorStyle.value = { opacity: '0', transform: `translateX(${offsetX}px)`, transition: 'none' };

    setTimeout(() => {
      indicatorStyle.value = {
        opacity: '1',
        transform: `translateX(${offsetX}px)`,
        transition: 'opacity var(--transition-normal), transform var(--transition-spring)'
      };
    }, 50);
  });
};

const moveIndicatorTo = (index) => {
  if (!isVisible.value) return;
  nextTick(() => {
    const items = getDockItems();
    const targetItem = items[index];
    if (!targetItem || !dock.value) return;
    const dockRect = dock.value.getBoundingClientRect();
    const itemRect = targetItem.getBoundingClientRect();
    const offsetX = itemRect.left - dockRect.left + (itemRect.width / 2) - 2;
    indicatorStyle.value = {
      opacity: '1',
      transform: `translateX(${offsetX}px)`,
      transition: 'all var(--transition-spring)'
    };
  });
};

// === GESTION ADDITIONAL APPS ===
const toggleAdditionalApps = () => {
  if (!showAdditionalApps.value) {
    additionalAppsInDOM.value = true;
    clearTimeout(additionalHideTimeout);
    nextTick(() => requestAnimationFrame(() => {
      showAdditionalApps.value = true;
      setupAdditionalDragEvents();
    }));
  } else {
    closeAdditionalApps();
  }
};

const closeAdditionalApps = () => {
  if (!showAdditionalApps.value) return;
  showAdditionalApps.value = false;
  clearTimeout(additionalHideTimeout);
  additionalHideTimeout = setTimeout(() => additionalAppsInDOM.value = false, 400);
};

const handleToggleClick = (event) => {
  if (event.target.closest('.toggle-icon')) event.stopPropagation();
  resetHideTimer();
  toggleAdditionalApps();
};

// === GESTION ADDITIONAL DRAG ===
const onAdditionalDragStart = (e) => {
  if (!showAdditionalApps.value) return;
  isDraggingAdditional = true;
  additionalDragMoved = false;
  additionalDragStartY = getEventY(e);
};

const setupAdditionalDragEvents = () => {
  const el = additionalAppsContainer.value;
  if (el) {
    el.addEventListener('mousedown', onAdditionalDragStart);
    el.addEventListener('touchstart', onAdditionalDragStart, { passive: false });
  }
};

const removeAdditionalDragEvents = () => {
  const el = additionalAppsContainer.value;
  if (el) {
    el.removeEventListener('mousedown', onAdditionalDragStart);
    el.removeEventListener('touchstart', onAdditionalDragStart);
  }
};

// === EFFETS VISUELS ===
const addPressEffect = (e) => {
  const button = e.target.closest('button');
  if (!button || button.disabled) return;
  button.classList.add('is-pressed');
  setTimeout(() => button.classList.remove('is-pressed'), 150);
};

// === CHARGEMENT CONFIG ===
const loadDockConfig = async () => {
  try {
    const response = await fetch('/api/settings/dock-apps');
    const data = await response.json();
    if (data.status === 'success') {
      enabledApps.value = data.config.enabled_apps || ["librespot", "bluetooth", "roc", "radio", "multiroom", "equalizer", "settings"];
    }
  } catch (error) {
    console.error('Error loading dock config:', error);
  }
};

const loadVolumeStepsConfig = async () => {
  try {
    const response = await fetch('/api/settings/volume-steps');
    const data = await response.json();
    if (data.status === 'success') {
      mobileVolumeSteps.value = data.config.mobile_volume_steps || 5;
    }
  } catch (error) {
    console.error('Error loading volume steps config:', error);
  }
};

// === ÉVÉNEMENTS GLOBAUX ===
const setupDragEvents = () => {
  const zone = dragZone.value;
  const dockEl = dock.value;
  if (!zone) return;

  zone.addEventListener('mousedown', onDragStart);
  zone.addEventListener('touchstart', onDragStart, { passive: false });
  zone.addEventListener('touchmove', e => e.preventDefault(), { passive: false });

  if (dockEl) {
    dockEl.addEventListener('mousedown', onDragStart);
    dockEl.addEventListener('touchstart', onDragStart, { passive: false });
  }

  document.addEventListener('mousemove', onDragMove);
  document.addEventListener('mouseup', onDragEnd);
  document.addEventListener('touchmove', onDragMove, { passive: false });
  document.addEventListener('touchend', onDragEnd);
  document.addEventListener('click', onClickOutside);
  document.addEventListener('pointerup', onVolumeHoldEnd);
  document.addEventListener('pointercancel', onVolumeHoldEnd);
};

const removeDragEvents = () => {
  const zone = dragZone.value;
  const dockEl = dock.value;

  if (zone) {
    zone.removeEventListener('mousedown', onDragStart);
    zone.removeEventListener('touchstart', onDragStart);
  }
  if (dockEl) {
    dockEl.removeEventListener('mousedown', onDragStart);
    dockEl.removeEventListener('touchstart', onDragStart);
  }

  removeAdditionalDragEvents();

  ['mousemove', 'mouseup', 'touchmove', 'touchend', 'click'].forEach(event => {
    document.removeEventListener(event, event === 'mousemove' ? onDragMove :
      event === 'mouseup' ? onDragEnd :
        event === 'touchmove' ? onDragMove :
          event === 'touchend' ? onDragEnd : onClickOutside);
  });

  document.removeEventListener('pointerup', onVolumeHoldEnd);
  document.removeEventListener('pointercancel', onVolumeHoldEnd);
};

// === LIFECYCLE ===
watch(() => unifiedStore.systemState.active_source, updateActiveIndicator);

onMounted(async () => {
  setupDragEvents();

  await Promise.all([loadDockConfig(), loadVolumeStepsConfig()]);

  // WebSocket listeners
  on('settings', 'dock_apps_changed', (message) => {
    if (message.data?.config?.enabled_apps) {
      enabledApps.value = message.data.config.enabled_apps;
    }
  });

  on('settings', 'volume_steps_changed', (message) => {
    if (message.data?.config?.mobile_volume_steps) {
      mobileVolumeSteps.value = message.data.config.mobile_volume_steps;
    }
  });

  on('volume', 'volume_changed', (message) => {
    if (message.data?.mobile_steps && message.data.mobile_steps !== mobileVolumeSteps.value) {
      mobileVolumeSteps.value = message.data.mobile_steps;
    }
  });

  setTimeout(() => showDragIndicator.value = true, 800);
});

onUnmounted(() => {
  removeDragEvents();
  clearAllTimers();
  onVolumeHoldEnd();
  resetGestureState();
});
</script>

<style scoped>
.drag-zone {
  position: fixed;
  width: 280px;
  bottom: 0;
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
  position: relative;
  margin-bottom: var(--space-03);
  left: 50%;
  transform: translateX(-50%) translateY(var(--space-06));
  z-index: 3998;
  border-radius: var(--radius-07);
  padding: var(--space-04);
  background: var(--color-background-glass);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  opacity: 0;
  pointer-events: none;
  transition: all var(--transition-spring);
  cursor: grab;
}

.additional-apps-container.visible {
  opacity: 1;
  transform: translateX(-50%) translateY(0);
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
  transition: all var(--transition-spring);
  opacity: 0;
  transform: translateY(20px) scale(0.95);
}

.additional-apps-container.visible .additional-app-content {
  opacity: 1;
  transform: translateY(0) scale(1);
}

.additional-apps-container.visible .additional-app-content:first-child {
  transition-delay: 0.1s;
}

.additional-apps-container.visible .additional-app-content:nth-child(2) {
  transition-delay: 0.137s;
}

.additional-apps-container.visible .additional-app-content:nth-child(3) {
  transition-delay: 0.175s;
}

.additional-apps-container.visible .additional-app-content:nth-child(4) {
  transition-delay: 0.212s;
}

.additional-apps-container.visible .additional-app-content:nth-child(5) {
  transition-delay: 0.250s;
}

.app-title {
  color: var(--color-text);
}

.dock-container {
  position: fixed;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%) translateY(148px) scale(0.85);
  z-index: 4000;
  transition: transform var(--transition-spring);
}

.dock-container.visible {
  transform: translateX(-50%) translateY(-29px) scale(1);
}

.dock {
  position: relative;
  border-radius: var(--radius-07);
  padding: var(--space-04);
  background: var(--color-background-glass);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-03);
  z-index: 0;
  overflow: hidden;
}

.dock::before,
.additional-apps-container::before {
  content: '';
  position: absolute;
  inset: 0;
  padding: 1.5px;
  background: var(--stroke-glass);
  border-radius: var(--radius-07);
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  z-index: -1;
  pointer-events: none;
}

.additional-app-content.button-interactive-subtle::before{
  content: '';
  position: absolute;
  inset: 0;
  padding: 1.5px;
  background: var(--stroke-glass);
  border-radius: var(--radius-04);
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  z-index: -1;
  pointer-events: none;
}

.volume-controls {
  display: flex;
  gap: var(--space-02);
  width: 100%;
  opacity: 0;
  transform: translateY(20px) scale(0.8);
  transition: all var(--transition-spring);
}

.volume-btn {
  display: flex;
  align-content: center;
  justify-content: center;
  flex: 1;
  background: var(--color-background-neutral-64);
  border-radius: var(--radius-04);
  cursor: pointer;
  color: var(--color-text-secondary);
  padding: var(--space-02);
  transition: all var(--transition-spring);
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
  background: var(--color-background-neutral-64);
  border-radius: var(--radius-full);
  opacity: 0;
  transform: translateY(20px) scale(0.8);
  transition: all var(--transition-spring);
}

.dock-item {
  cursor: pointer;
  opacity: 0;
  transform: translateY(20px) scale(0.8);
  transition: all var(--transition-spring);
  background: none;
  border: none;
}

.toggle-btn {
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-background-neutral-64);
  border-radius: var(--radius-03);
  padding: 0 var(--space-01);
  color: var(--color-text-secondary);
}

.dock-container.visible .dock-item,
.dock-container.visible .dock-separator,
.dock-container.visible .volume-controls {
  opacity: 1;
  transform: translateY(0) scale(1);
}

.dock-container.visible .volume-controls {
  transition-delay: 0.1s;
}

.dock-container.visible.fully-visible .dock-item,
.dock-container.visible.fully-visible .dock-separator,
.dock-container.visible.fully-visible .volume-controls {
  transition-delay: 0s !important;
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
  transition: opacity var(--transition-slow), transform var(--transition-spring);
}

.dock-item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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

  .dock-indicator {
    position: fixed;
    bottom: var(--space-03);
    left: 50%;
    transform: translateX(-50%);
    width: var(--space-05);
    height: var(--space-01);
    background: var(--color-background-glass);
    border-radius: var(--radius-full);
    z-index: 998;
    opacity: 0;
    pointer-events: none;
    transition: opacity 600ms ease-in-out;
    cursor: pointer;
  }

  .dock-indicator.visible {
    opacity: 1;
    pointer-events: auto;
  }

  .dock-indicator.hidden {
    opacity: 0 !important;
    transition: opacity var(--transition-normal) !important;
  }
}

.ios-app .drag-zone {
  height: var(--space-09);
}

.ios-app .dock-indicator {
  bottom: var(--space-08);
}

.ios-app .dock-container.visible {
  transform: translate(-50%) translateY(-64px) scale(1);
}

.android-app .dock-indicator {
  bottom: var(--space-07);
}

.android-app .dock-container.visible {
  transform: translate(-50%) translateY(-48px) scale(1);
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

  .dock-indicator {
    display: none;
  }
}

.volume-btn.is-pressed,
.dock-item.is-pressed {
  transform: scale(0.92) !important;
  opacity: 0.8 !important;
  transition-delay: 0s !important;
}

.additional-app-content.is-pressed {
  transform: scale(0.97) !important;
  opacity: 0.8 !important;
  transition-delay: 0s !important;
}

button:disabled.is-pressed {
  transform: none !important;
  opacity: 0.5 !important;
}
</style>