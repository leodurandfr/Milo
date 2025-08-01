<!-- frontend/src/components/navigation/BottomNavigation.vuz -->
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

      <button v-for="{ id, icon, title, handler } in ADDITIONAL_ACTIONS" :key="id" @click="handler"
        @touchstart="addPressEffect" @mousedown="addPressEffect"
        class="additional-app-content button-interactive-subtle">
        <AppIcon :name="icon" :size="32" />
        <div class="app-title heading-2">{{ title }}</div>
      </button>
    </div>

    <div ref="dock" class="dock">
      <!-- Volume Controls - Mobile uniquement -->
      <div class="volume-controls mobile-only">
        <button v-for="{ icon, handler } in VOLUME_CONTROLS" :key="icon" @click="handler" @touchstart="addPressEffect"
          @mousedown="addPressEffect" class="volume-btn button-interactive-subtle">
          <Icon :name="icon" :size="32" />
        </button>
      </div>

      <!-- App Container -->
      <div class="app-container">
        <!-- Sources Audio -->
        <button v-for="({ id, icon }, index) in AUDIO_SOURCES" :key="id" :ref="el => dockItems[index] = el"
          @click="() => handleSourceClick(id, index)" @touchstart="addPressEffect" @mousedown="addPressEffect"
          :disabled="unifiedStore.isTransitioning" class="dock-item button-interactive-subtle">
          <AppIcon :name="icon" size="large" class="dock-item-icon" />
        </button>

        <!-- Séparateur -->
        <div class="dock-separator"></div>

        <!-- Toggle Additional Apps - Mobile uniquement -->
        <button @click="handleToggleClick" @touchstart="addPressEffect" @mousedown="addPressEffect"
          class="dock-item toggle-btn mobile-only button-interactive">
          <Icon :name="showAdditionalApps ? 'closeDots' : 'threeDots'" :size="32" class="toggle-icon" />
        </button>

        <!-- Actions Desktop -->
        <button v-for="{ id, icon, handler } in ADDITIONAL_ACTIONS" :key="`desktop-${id}`" @click="handler"
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
import { ref, onMounted, onUnmounted, computed, watch, nextTick } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import AppIcon from '@/components/ui/AppIcon.vue';
import Icon from '@/components/ui/Icon.vue';

// === CONFIGURATION ===
const AUDIO_SOURCES = [
  { id: 'librespot', icon: 'spotify' },
  { id: 'bluetooth', icon: 'bluetooth' },
  { id: 'roc', icon: 'roc' }
];

const ADDITIONAL_ACTIONS = [
  { id: 'multiroom', icon: 'multiroom', title: 'Multiroom', handler: () => emit('open-snapcast') },
  { id: 'equalizer', icon: 'equalizer', title: 'Égaliseur', handler: () => emit('open-equalizer') }
];

// Store unifié pour volume ET audio
const unifiedStore = useUnifiedAudioStore();

const VOLUME_CONTROLS = [
  { icon: 'minus', handler: () => unifiedStore.decreaseVolume() },
  { icon: 'plus', handler: () => unifiedStore.increaseVolume() }
];

// === ÉMISSIONS ===
const emit = defineEmits(['open-snapcast', 'open-equalizer']);

// === REFS ===
const dragZone = ref(null);
const dockContainer = ref(null);
const dock = ref(null);
const activeIndicator = ref(null);
const additionalAppsContainer = ref(null);
const dockItems = ref([]);

// === ÉTAT ===
const isVisible = ref(false);
const isFullyVisible = ref(false);
const isDragging = ref(false);
const showAdditionalApps = ref(false);
const showDragIndicator = ref(false);
const additionalAppsInDOM = ref(false);

// === VARIABLES INTERNES ===
let dragStartY = 0, dragCurrentY = 0, hideTimeout = null, additionalHideTimeout = null;
let isDraggingAdditional = false, additionalDragStartY = 0, clickTimeout = null;

// === COMPUTED ===
const activeSourceIndex = computed(() =>
  AUDIO_SOURCES.findIndex(source => source.id === unifiedStore.currentSource)
);

const indicatorStyle = ref({
  opacity: '0',
  transform: 'translateX(0px)',
  transition: 'all var(--transition-spring)'
});

// === UTILITAIRES ===
const getEventY = (e) => e.type.includes('touch')
  ? (e.touches[0]?.clientY || e.changedTouches[0]?.clientY) : e.clientY;

const isDesktop = () => window.matchMedia('not (max-aspect-ratio: 4/3)').matches;

const clearAllTimers = () => {
  [hideTimeout, additionalHideTimeout, clickTimeout].forEach(clearTimeout);
};

const startHideTimer = () => {
  clearTimeout(hideTimeout);
  hideTimeout = setTimeout(hideDock, 10000);
};

const resetHideTimer = () => isVisible.value && startHideTimer();

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
  dragCurrentY = getEventY(e);
  const deltaY = dragStartY - dragCurrentY;

  if (Math.abs(deltaY) >= 30) {
    if (deltaY > 0 && !isVisible.value) showDock();
    else if (deltaY < 0 && isVisible.value) hideDock();
    isDragging.value = false;
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
};

// === LIFECYCLE ===
watch(() => unifiedStore.currentSource, updateActiveIndicator);

onMounted(() => {
  setupDragEvents();
  setTimeout(() => showDragIndicator.value = true, 800);
});

onUnmounted(() => {
  removeDragEvents();
  clearAllTimers();
});
</script>

<!-- STYLES IDENTIQUES - pas de changements CSS -->
<style scoped>
/* [Tous les styles CSS restent identiques] */
.drag-zone {
  position: fixed;
  width: 280px;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  height: 32%;
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
  transform: translateX(-50%) translateY(-20px) scale(1);
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