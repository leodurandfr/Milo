<!-- frontend/src/components/navigation/BottomNavigation.vue - Drag + Hold-to-Repeat SIMPLIFIÉ -->
<template>
  <!-- Zone de drag invisible -->
  <div ref="dragZone" class="drag-zone" :class="{ dragging: isDragging }" @click.stop="onDragZoneClick"></div>

  <!-- Indicateur de drag -->
  <div class="dock-indicator" :class="{ hidden: isVisible, visible: showDragIndicator }" @click.stop="onIndicatorClick">
  </div>

  <!-- Dock de navigation -->
  <nav ref="dockContainer" class="dock-container" :class="{ visible: isVisible, 'fully-visible': isFullyVisible }">
    <!-- Additional Apps - Mobile uniquement -->
    <div v-if="additionalAppsInDOM" ref="additionalAppsContainer" class="additional-apps-container mobile-only"
      :class="{ visible: showAdditionalApps }">

      <button v-for="{ id, icon, title, handler } in enabledAdditionalActions" :key="id" @click="handler"
        @touchstart="addPressEffect" @mousedown="addPressEffect"
        class="additional-app-content button-interactive-subtle">
        <AppIcon :name="icon" :size="32" />
        <div class="app-title heading-2">{{ title }}</div>
      </button>
    </div>

    <div ref="dock" class="dock">
      <!-- Volume Controls - Mobile uniquement avec hold-to-repeat -->
      <div class="volume-controls mobile-only">
        <button v-for="{ icon, handler, delta } in volumeControlsWithSteps" :key="icon" 
          @mousedown="(e) => onVolumeHoldStart(delta, e)" 
          @touchstart="(e) => onVolumeHoldStart(delta, e)"
          @mouseup="onVolumeHoldEnd"
          @touchend="onVolumeHoldEnd"
          @mouseleave="onVolumeHoldEnd"
          @touchcancel="onVolumeHoldEnd"
          class="volume-btn button-interactive-subtle">
          <Icon :name="icon" :size="32" />
        </button>
      </div>

      <!-- App Container -->
      <div class="app-container">
        <!-- Sources Audio -->
        <button v-for="({ id, icon }, index) in enabledAudioSources" :key="id" :ref="el => dockItems[index] = el"
          @click="() => handleSourceClick(id, index)" @touchstart="addPressEffect" @mousedown="addPressEffect"
          :disabled="unifiedStore.isTransitioning" class="dock-item button-interactive-subtle">
          <AppIcon :name="icon" size="large" class="dock-item-icon" />
        </button>

        <!-- Séparateur (seulement si on a des additional actions sur desktop) -->
        <div v-if="enabledAdditionalActions.length > 0" class="dock-separator"></div>

        <!-- Toggle Additional Apps - Mobile uniquement (seulement si on a des additional actions) -->
        <button v-if="enabledAdditionalActions.length > 0" @click="handleToggleClick" @touchstart="addPressEffect" @mousedown="addPressEffect"
          class="dock-item toggle-btn mobile-only button-interactive">
          <Icon :name="showAdditionalApps ? 'closeDots' : 'threeDots'" :size="32" class="toggle-icon" />
        </button>

        <!-- Actions Desktop -->
        <button v-for="{ id, icon, handler } in enabledAdditionalActions" :key="`desktop-${id}`" @click="handler"
          @touchstart="addPressEffect" @mousedown="addPressEffect"
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
const ALL_AUDIO_SOURCES = [
  { id: 'librespot', icon: 'spotify' },
  { id: 'bluetooth', icon: 'bluetooth' },
  { id: 'roc', icon: 'roc' }
];

const ALL_ADDITIONAL_ACTIONS = [
  { id: 'multiroom', icon: 'multiroom', title: computed(() => t('Multiroom')), handler: () => emit('open-snapcast') },
  { id: 'equalizer', icon: 'equalizer', title: computed(() => t('Égaliseur')), handler: () => emit('open-equalizer') }
];

// === CONFIGURATION DYNAMIQUE ===
const enabledApps = ref(["librespot", "bluetooth", "roc", "multiroom", "equalizer"]);
const mobileVolumeSteps = ref(5);

// Sources audio filtrées selon la config
const enabledAudioSources = computed(() => 
  ALL_AUDIO_SOURCES.filter(source => enabledApps.value.includes(source.id))
);

// Actions additionnelles filtrées selon la config
const enabledAdditionalActions = computed(() => 
  ALL_ADDITIONAL_ACTIONS.filter(action => enabledApps.value.includes(action.id))
);

// Store unifié pour volume ET audio
const unifiedStore = useUnifiedAudioStore();

// Volume controls avec steps configurables et delta pour hold-to-repeat
const volumeControlsWithSteps = computed(() => [
  { icon: 'minus', handler: () => unifiedStore.adjustVolume(-mobileVolumeSteps.value), delta: -mobileVolumeSteps.value },
  { icon: 'plus', handler: () => unifiedStore.adjustVolume(mobileVolumeSteps.value), delta: mobileVolumeSteps.value }
]);

// === ÉMISSIONS ===
const emit = defineEmits(['open-snapcast', 'open-equalizer']);

// === REFS ===
const dragZone = ref(null);
const dockContainer = ref(null);
const dock = ref(null);
const activeIndicator = ref(null);
const additionalAppsContainer = ref(null);
const dockItems = ref([]);

// === ÉTAT DOCK ===
const isVisible = ref(false);
const isFullyVisible = ref(false);
const isDragging = ref(false);
const showAdditionalApps = ref(false);
const showDragIndicator = ref(false);
const additionalAppsInDOM = ref(false);

// === HOLD-TO-REPEAT VOLUME ===
const volumeStartTimer = ref(null);
const volumeRepeatTimer = ref(null);
const isVolumeHolding = ref(false);
const currentVolumeDelta = ref(0);

// === DRAG VS HOLD DETECTION ===
const gestureHasMoved = ref(false);
const gestureStartPosition = ref({ x: 0, y: 0 });
const MOVE_THRESHOLD = 10;

// === VARIABLES INTERNES ===
let dragStartY = 0, dragCurrentY = 0, hideTimeout = null, additionalHideTimeout = null;
let isDraggingAdditional = false, additionalDragStartY = 0, clickTimeout = null;

// === COMPUTED ===
const activeSourceIndex = computed(() =>
  enabledAudioSources.value.findIndex(source => source.id === unifiedStore.currentSource)
);

const indicatorStyle = ref({
  opacity: '0',
  transform: 'translateX(0px)',
  transition: 'all var(--transition-spring)'
});

// === UTILITAIRES ===
const getEventY = (e) => e.type.includes('touch')
  ? (e.touches[0]?.clientY || e.changedTouches[0]?.clientY) : e.clientY;

const getEventX = (e) => e.type.includes('touch')
  ? (e.touches[0]?.clientX || e.changedTouches[0]?.clientX) : e.clientX;

const isDesktop = () => window.matchMedia('not (max-aspect-ratio: 4/3)').matches;

const clearAllTimers = () => {
  [hideTimeout, additionalHideTimeout, clickTimeout, volumeStartTimer.value, volumeRepeatTimer.value].forEach(clearTimeout);
  volumeStartTimer.value = null;
  volumeRepeatTimer.value = null;
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

// === HOLD-TO-REPEAT VOLUME ===
const onVolumeHoldStart = (delta, event) => {
  // Stocker position de départ
  gestureStartPosition.value = {
    x: getEventX(event),
    y: getEventY(event)
  };
  gestureHasMoved.value = false;
  currentVolumeDelta.value = delta;
  
  // Effet visuel immédiat
  addPressEffect(event);
  
  // Timer de 150ms pour confirmer que c'est un hold (pas un swipe)
  volumeStartTimer.value = setTimeout(() => {
    if (!gestureHasMoved.value) {
      // Pas de mouvement = c'est un hold, commencer le volume
      unifiedStore.adjustVolume(delta);
      isVolumeHolding.value = true;
      
      // Démarrer la répétition après 300ms
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
  }, 150);
  
  resetHideTimer();
};

const onVolumeHoldEnd = () => {
  // Si c'est un click rapide (relâcher avant 150ms, sans mouvement)
  if (volumeStartTimer.value && !gestureHasMoved.value && currentVolumeDelta.value !== 0) {
    // Click rapide détecté = ajuster volume immédiatement
    unifiedStore.adjustVolume(currentVolumeDelta.value);
  }
  
  isVolumeHolding.value = false;
  
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
};

// === ACTIONS ===
const handleSourceClick = (sourceId, index) => {
  resetHideTimer();
  moveIndicatorTo(index);
  unifiedStore.changeSource(sourceId);
};

// === INDICATEUR ACTIF ===
const updateActiveIndicator = () => {
  if (!isVisible.value || activeSourceIndex.value === -1) {
    indicatorStyle.value.opacity = '0';
    return;
  }

  nextTick(() => {
    const targetItem = dockItems.value[activeSourceIndex.value];
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
    const targetItem = dockItems.value[index];
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

// === GESTION DES ADDITIONAL APPS ===
const toggleAdditionalApps = () => {
  if (!showAdditionalApps.value) {
    additionalAppsInDOM.value = true;
    clearTimeout(additionalHideTimeout);
    nextTick(() => requestAnimationFrame(() => {
      showAdditionalApps.value = true;
      setupAdditionalDragEvents();
    }));
  } else {
    showAdditionalApps.value = false;
    clearTimeout(additionalHideTimeout);
    additionalHideTimeout = setTimeout(() => additionalAppsInDOM.value = false, 400);
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

// === GESTION DU DOCK ===
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

// === GESTION DES CLICS ===
const onClickOutside = (event) => {
  if (!isVisible.value) return;
  if (dockContainer.value && dockContainer.value.contains(event.target)) return;
  const isModalClick = event.target.closest('.modal-overlay, .modal-container, .modal-content');
  if (isModalClick) return;
  hideDock();
};

const onDragZoneClick = () => {
  if (isDesktop()) return;
  if (!isDragging.value && !isVisible.value) {
    showDock();
  }
};

const onIndicatorClick = () => {
  if (isDesktop()) return;
  if (!isDragging.value && !isVisible.value) {
    showDock();
  }
};

// === GESTION DU DRAG ===
const onDragStart = (e) => {
  isDragging.value = true;
  dragStartY = getEventY(e);
  dragCurrentY = dragStartY;
  resetGestureState();
};

const onDragMove = (e) => {
  if (isDraggingAdditional) {
    const deltaY = getEventY(e) - additionalDragStartY;
    if (Math.abs(deltaY) >= 30 && deltaY > 0) {
      closeAdditionalApps();
      isDraggingAdditional = false;
    }
    return;
  }

  if (!isDragging.value) return;
  
  const currentY = getEventY(e);
  const currentX = getEventX(e);
  
  // Vérifier mouvement pour annuler volume hold si nécessaire
  if (!gestureHasMoved.value) {
    const deltaX = Math.abs(currentX - gestureStartPosition.value.x);
    const deltaY = Math.abs(currentY - gestureStartPosition.value.y);
    
    if (deltaX > MOVE_THRESHOLD || deltaY > MOVE_THRESHOLD) {
      gestureHasMoved.value = true;
      onVolumeHoldEnd(); // Annuler le volume hold
    }
  }
  
  // Traitement normal du drag pour le dock
  dragCurrentY = currentY;
  const deltaY = dragStartY - dragCurrentY;

  if (Math.abs(deltaY) >= 30) {
    if (deltaY > 0 && !isVisible.value) showDock();
    else if (deltaY < 0 && isVisible.value) hideDock();
    isDragging.value = false;
    resetGestureState();
  }
};

const onDragEnd = () => {
  clearTimeout(clickTimeout);
  if (isDraggingAdditional) {
    isDraggingAdditional = false;
    return;
  }
  isDragging.value = false;
  resetHideTimer();
  resetGestureState();
};

// === GESTION DU DRAG ADDITIONAL APPS ===
const onAdditionalDragStart = (e) => {
  if (!showAdditionalApps.value) return;
  isDraggingAdditional = true;
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

// === CHARGEMENT CONFIG INITIALE ===
async function loadDockConfig() {
  try {
    const response = await fetch('/api/settings/dock-apps');
    const data = await response.json();
    if (data.status === 'success') {
      enabledApps.value = data.config.enabled_apps || ["librespot", "bluetooth", "roc", "multiroom", "equalizer"];
    }
  } catch (error) {
    console.error('Error loading dock config:', error);
  }
}

async function loadVolumeStepsConfig() {
  try {
    const response = await fetch('/api/settings/volume-steps');
    const data = await response.json();
    if (data.status === 'success') {
      mobileVolumeSteps.value = data.config.mobile_volume_steps || 5;
    }
  } catch (error) {
    console.error('Error loading volume steps config:', error);
  }
}

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
  
  // Événements globaux pour arrêter le volume hold
  document.addEventListener('mouseup', onVolumeHoldEnd);
  document.addEventListener('touchend', onVolumeHoldEnd);
  document.addEventListener('touchcancel', onVolumeHoldEnd);
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
  document.removeEventListener('mousemove', onDragMove);
  document.removeEventListener('mouseup', onDragEnd);
  document.removeEventListener('touchmove', onDragMove);
  document.removeEventListener('touchend', onDragEnd);
  document.removeEventListener('click', onClickOutside);
  
  document.removeEventListener('mouseup', onVolumeHoldEnd);
  document.removeEventListener('touchend', onVolumeHoldEnd);
  document.removeEventListener('touchcancel', onVolumeHoldEnd);
};

// === LIFECYCLE ===
watch(() => unifiedStore.currentSource, updateActiveIndicator);

onMounted(async () => {
  setupDragEvents();
  
  await loadDockConfig();
  await loadVolumeStepsConfig();
  
  on('settings', 'dock_apps_changed', (message) => {
    if (message.data?.config?.enabled_apps) {
      enabledApps.value = message.data.config.enabled_apps;
      console.log('Dock apps config updated:', enabledApps.value);
    }
  });
  
  on('settings', 'volume_steps_changed', (message) => {
    if (message.data?.config?.mobile_volume_steps) {
      mobileVolumeSteps.value = message.data.config.mobile_volume_steps;
      console.log('Volume steps config updated via settings:', mobileVolumeSteps.value);
    }
  });
  
  on('volume', 'volume_changed', (message) => {
    if (message.data?.mobile_steps && message.data.mobile_steps !== mobileVolumeSteps.value) {
      mobileVolumeSteps.value = message.data.mobile_steps;
      console.log('Volume steps updated via volume event:', mobileVolumeSteps.value);
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
/* [Tous les styles CSS restent identiques] */
.drag-zone {
  position: fixed;
  width: 280px;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  height: 36%;
  opacity: 0.2;
  z-index: 999;
  cursor: grab;
  user-select: none;
}

.drag-zone.dragging {
  cursor: grabbing;
}

.additional-apps-container {
  position: relative;
  margin-bottom: var(--space-03);
  left: 50%;
  transform: translateX(-50%) translateY(var(--space-06));
  z-index: 998;
  border-radius: var(--radius-06);
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

.additional-apps-container::before {
  content: '';
  position: absolute;
  inset: 0;
  padding: 2px;
  background: var(--stroke-glass);
  border-radius: var(--radius-06);
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  z-index: -1;
  pointer-events: none;
}

.additional-app-content {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-02);
  width: 100%;
  background: var(--color-background-neutral-64);
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
  transition-delay: 0.2s;
}

.app-title {
  color: var(--color-text);
}

.dock-container {
  position: fixed;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%) translateY(148px) scale(0.85);
  z-index: 1000;
  transition: transform var(--transition-spring);
}

.dock-container.visible {
  transform: translateX(-50%) translateY(-29px) scale(1);
}

.dock {
  position: relative;
  border-radius: var(--radius-06);
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

.dock::before {
  content: '';
  position: absolute;
  inset: 0;
  padding: 2px;
  background: var(--stroke-glass);
  border-radius: var(--radius-06);
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

.dock-container.visible .app-container> :nth-child(1) {
  transition-delay: 0.1s;
}

.dock-container.visible .app-container> :nth-child(2) {
  transition-delay: 0.15s;
}

.dock-container.visible .app-container> :nth-child(3) {
  transition-delay: 0.2s;
}

.dock-container.visible .app-container> :nth-child(4) {
  transition-delay: 0.225s;
}

.dock-container.visible .app-container> :nth-child(5) {
  transition-delay: 0.25s;
}

.dock-container.visible .app-container> :nth-child(6) {
  transition-delay: 0.25s;
}

.dock-container.visible .app-container> :nth-child(7) {
  transition-delay: 0.3s;
}

.dock-container.visible.fully-visible .dock-item,
.dock-container.visible.fully-visible .dock-separator,
.dock-container.visible.fully-visible .volume-controls,
.dock-container.visible.fully-visible .app-container>* {
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