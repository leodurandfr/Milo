<!-- frontend/src/components/navigation/BottomNavigation.vue - Version optimisée finale -->
<template>
  <!-- Zone de drag invisible -->
  <div ref="dragZone" class="drag-zone" :class="{ dragging: isDragging }" @click="onDragZoneClick"></div>

  <!-- Indicateur de drag -->
  <div class="dock-indicator" :class="{ hidden: isVisible, visible: showDragIndicator }"></div>

  <!-- Dock de navigation -->
  <nav ref="dockContainer" class="dock-container" :class="{ visible: isVisible, 'fully-visible': isFullyVisible }">
    <!-- Additional Apps - Mobile uniquement -->
    <div v-if="additionalAppsInDOM" 
         ref="additionalAppsContainer"
         class="additional-apps-container mobile-only"
         :class="{ visible: showAdditionalApps }">
      <!-- Multiroom -->
      <button @click="openSnapcast" @touchstart="addPressEffect" @mousedown="addPressEffect" 
              class="additional-app-content button-interactive-subtle">
        <AppIcon name="multiroom" :size="32" />
        <div class="app-title heading-2">Multiroom</div>
      </button>

      <!-- Equalizer -->
      <button @click="openEqualizer" @touchstart="addPressEffect" @mousedown="addPressEffect" 
              class="additional-app-content button-interactive-subtle">
        <AppIcon name="equalizer" :size="32" />
        <div class="app-title heading-2">Égaliseur</div>
      </button>
    </div>
    
    <div ref="dock" class="dock">
      <!-- Volume Controls - Mobile uniquement -->
      <div class="volume-controls mobile-only">
        <button @click="handleVolumeDown" @touchstart="addPressEffect" @mousedown="addPressEffect"
                class="volume-btn button-interactive-subtle" :disabled="volumeStore.isAdjusting">
          <Icon name="minus" :size="32" />
        </button>
        <button @click="handleVolumeUp" @touchstart="addPressEffect" @mousedown="addPressEffect"
                class="volume-btn button-interactive-subtle" :disabled="volumeStore.isAdjusting">
          <Icon name="plus" :size="32" />
        </button>
      </div>

      <!-- App Container -->
      <div class="app-container">
        <!-- Sources Audio -->
        <button v-for="(source, index) in audioSources" :key="source.id"
                :ref="el => dockItems[index] = el"
                @click="() => handleSourceClick(source.id, index)"
                @touchstart="addPressEffect" @mousedown="addPressEffect" 
                :disabled="unifiedStore.isTransitioning"
                class="dock-item button-interactive-subtle">
          <AppIcon :name="source.icon" size="large" class="dock-item-icon" />
        </button>

        <!-- Séparateur -->
        <div ref="separator" class="dock-separator"></div>

        <!-- Toggle Additional Apps - Mobile uniquement -->
        <button ref="dockToggle" @click="handleToggleClick"
                @touchstart="addPressEffect" @mousedown="addPressEffect"
                class="dock-item toggle-btn mobile-only button-interactive">
          <Icon :name="showAdditionalApps ? 'closeDots' : 'threeDots'" :size="32" class="toggle-icon" />
        </button>

        <!-- Actions Desktop -->
        <button v-for="action in desktopActions" :key="action.id"
                @click="action.handler" @touchstart="addPressEffect" @mousedown="addPressEffect"
                class="dock-item desktop-only button-interactive-subtle">
          <AppIcon :name="action.icon" size="large" class="dock-item-icon" />
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
import { useVolumeStore } from '@/stores/volumeStore';
import AppIcon from '@/components/ui/AppIcon.vue';
import Icon from '@/components/ui/Icon.vue';

// === CONFIGURATION ===
const ANIMATION_DURATION = 400; // Durée des animations en ms
const AUTO_HIDE_DELAY = 10000; // Délai auto-masquage en ms
const DRAG_THRESHOLD = 30; // Seuil de drag en pixels

const audioSources = [
  { id: 'librespot', icon: 'spotify' },
  { id: 'bluetooth', icon: 'bluetooth' },
  { id: 'roc', icon: 'roc' }
];

const desktopActions = [
  { id: 'multiroom', icon: 'multiroom', handler: () => openSnapcast() },
  { id: 'equalizer', icon: 'equalizer', handler: () => openEqualizer() }
];

// === ÉMISSIONS ===
const emit = defineEmits(['open-snapcast', 'open-equalizer']);

// === STORES ===
const unifiedStore = useUnifiedAudioStore();
const volumeStore = useVolumeStore();

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
let dragStartY = 0;
let hideTimeout = null;
let additionalHideTimeout = null;
let isDraggingAdditional = false;
let additionalDragStartY = 0;

// === INDICATEUR ACTIF ===
const indicatorStyle = ref({
  opacity: '0',
  transform: 'translateX(0px)',
  transition: 'all var(--transition-spring)'
});

const activeSourceIndex = computed(() => {
  return audioSources.findIndex(source => source.id === unifiedStore.currentSource);
});

function updateActiveIndicator() {
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

    indicatorStyle.value = {
      opacity: '0',
      transform: `translateX(${offsetX}px)`,
      transition: 'none'
    };

    setTimeout(() => {
      indicatorStyle.value = {
        opacity: '1',
        transform: `translateX(${offsetX}px)`,
        transition: 'opacity var(--transition-normal), transform var(--transition-spring)'
      };
    }, 50);
  });
}

function moveIndicatorTo(index) {
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
}

// === GESTION DES TIMERS ===
function startHideTimer() {
  clearTimeout(hideTimeout);
  hideTimeout = setTimeout(hideDock, AUTO_HIDE_DELAY);
}

function resetHideTimer() {
  if (isVisible.value) startHideTimer();
}

// === ACTIONS ===
function openSnapcast() {
  resetHideTimer();
  emit('open-snapcast');
}

function openEqualizer() {
  resetHideTimer();
  emit('open-equalizer');
}

function handleSourceClick(sourceId, index) {
  resetHideTimer();
  moveIndicatorTo(index);
  unifiedStore.changeSource(sourceId);
}

function handleVolumeUp() {
  resetHideTimer();
  volumeStore.increaseVolume();
}

function handleVolumeDown() {
  resetHideTimer();
  volumeStore.decreaseVolume();
}

// === GESTION DES ADDITIONAL APPS ===
function toggleAdditionalApps() {
  if (!showAdditionalApps.value) {
    additionalAppsInDOM.value = true;
    clearTimeout(additionalHideTimeout);
    
    nextTick(() => {
      requestAnimationFrame(() => {
        showAdditionalApps.value = true;
        setupAdditionalDragEvents();
      });
    });
  } else {
    showAdditionalApps.value = false;
    clearTimeout(additionalHideTimeout);
    additionalHideTimeout = setTimeout(() => {
      additionalAppsInDOM.value = false;
    }, ANIMATION_DURATION);
  }
}

function closeAdditionalApps() {
  if (!showAdditionalApps.value) return;
  
  showAdditionalApps.value = false;
  clearTimeout(additionalHideTimeout);
  additionalHideTimeout = setTimeout(() => {
    additionalAppsInDOM.value = false;
  }, ANIMATION_DURATION);
}

function handleToggleClick(event) {
  if (event.target.closest('.toggle-icon')) {
    event.stopPropagation();
  }
  resetHideTimer();
  toggleAdditionalApps();
}

// === GESTION DU DOCK ===
function showDock() {
  if (isVisible.value) return;
  
  isVisible.value = true;
  isFullyVisible.value = false;
  startHideTimer();

  setTimeout(() => {
    isFullyVisible.value = true;
  }, ANIMATION_DURATION);

  setTimeout(() => {
    updateActiveIndicator();
  }, ANIMATION_DURATION + 100);
}

function hideDock() {
  if (!isVisible.value) return;

  isFullyVisible.value = false;
  showAdditionalApps.value = false;
  isVisible.value = false;
  clearTimeout(hideTimeout);
  clearTimeout(additionalHideTimeout);
  indicatorStyle.value.opacity = '0';
  additionalAppsInDOM.value = false;
}

// === GESTION DES CLICS EXTÉRIEURS ===
function onClickOutside(event) {
  if (!isVisible.value) return;
  
  const dock = dockContainer.value;
  if (dock && !dock.contains(event.target)) {
    hideDock();
  }
}

function onDragZoneClick() {
  if (!isDragging.value && !isVisible.value) {
    showDock();
  }
}

// === GESTION DU DRAG ===
function getEventY(e) {
  return e.type.includes('touch') 
    ? (e.touches[0]?.clientY || e.changedTouches[0]?.clientY)
    : e.clientY;
}

function onDragStart(e) {
  isDragging.value = true;
  dragStartY = getEventY(e);
}

function onDragMove(e) {
  if (isDraggingAdditional) {
    const deltaY = getEventY(e) - additionalDragStartY;
    if (Math.abs(deltaY) >= DRAG_THRESHOLD && deltaY > 0) {
      closeAdditionalApps();
      isDraggingAdditional = false;
    }
    return;
  }

  if (!isDragging.value) return;

  const deltaY = dragStartY - getEventY(e);
  if (Math.abs(deltaY) >= DRAG_THRESHOLD) {
    if (deltaY > 0 && !isVisible.value) {
      showDock();
    } else if (deltaY < 0 && isVisible.value) {
      hideDock();
    }
    isDragging.value = false;
  }
}

function onDragEnd() {
  if (isDraggingAdditional) {
    isDraggingAdditional = false;
    return;
  }
  
  isDragging.value = false;
  resetHideTimer();
}

// === GESTION DU DRAG ADDITIONAL APPS ===
function onAdditionalDragStart(e) {
  if (!showAdditionalApps.value) return;
  isDraggingAdditional = true;
  additionalDragStartY = getEventY(e);
}

function setupAdditionalDragEvents() {
  const additionalEl = additionalAppsContainer.value;
  if (additionalEl) {
    additionalEl.addEventListener('mousedown', onAdditionalDragStart);
    additionalEl.addEventListener('touchstart', onAdditionalDragStart, { passive: false });
  }
}

function removeAdditionalDragEvents() {
  const additionalEl = additionalAppsContainer.value;
  if (additionalEl) {
    additionalEl.removeEventListener('mousedown', onAdditionalDragStart);
    additionalEl.removeEventListener('touchstart', onAdditionalDragStart);
  }
}

// === EFFETS VISUELS ===
function addPressEffect(e) {
  const button = e.target.closest('button');
  if (!button || button.disabled) return;

  button.classList.add('is-pressed');
  setTimeout(() => {
    button.classList.remove('is-pressed');
  }, 150);
}

// === ÉVÉNEMENTS GLOBAUX ===
function setupDragEvents() {
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
}

function removeDragEvents() {
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
}

// === WATCHERS ET LIFECYCLE ===
watch(() => unifiedStore.currentSource, updateActiveIndicator);

onMounted(() => {
  setupDragEvents();
  setTimeout(() => {
    showDragIndicator.value = true;
  }, 800);
});

onUnmounted(() => {
  removeDragEvents();
  clearTimeout(hideTimeout);
  clearTimeout(additionalHideTimeout);
});
</script>

<style scoped>
/* Zone de drag invisible */
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

/* Additional Apps Container - Mobile uniquement */
.additional-apps-container {
  position: relative;
  margin-bottom: var(--space-03);
  left: 50%;
  transform: translateX(-50%) translateY(var(--space-06));
  z-index: 998;
  border-radius: var(--radius-06);
  padding: var(--space-04);
  background: var(--color-background-glass);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
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

/* Dock container avec animation spring */
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

/* Dock avec design-system */
.dock {
  position: relative;
  border-radius: var(--radius-06);
  padding: var(--space-04);
  background: var(--color-background-glass);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
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

/* Volume Controls - Mobile uniquement */
.volume-controls {
  display: flex;
  gap: var(--space-02);
  width: 100%;
  opacity: 0;
  transform: translateY(20px) scale(0.8);
  transition: all var(--transition-spring);
}

.volume-btn {
  flex: 1;
  background: var(--color-background-neutral-64);
  border-radius: var(--radius-04);
  cursor: pointer;
  color: var(--color-text-secondary);
  padding: var(--space-02);
  transition: all var(--transition-spring);
}

/* App Container */
.app-container {
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

/* Séparateur */
.dock-separator {
  width: 2px;
  height: var(--space-07);
  background: var(--color-background-neutral-64);
  border-radius: var(--radius-full);
  opacity: 0;
  transform: translateY(20px) scale(0.8);
  transition: all var(--transition-spring);
}

/* Items du dock */
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

/* Animation staggerée */
.dock-container.visible .dock-item,
.dock-container.visible .dock-separator,
.dock-container.visible .volume-controls {
  opacity: 1;
  transform: translateY(0) scale(1);
}

.dock-container.visible .volume-controls {
  transition-delay: 0.1s;
}

.dock-container.visible .app-container> :nth-child(1) { transition-delay: 0.1s; }
.dock-container.visible .app-container> :nth-child(2) { transition-delay: 0.15s; }
.dock-container.visible .app-container> :nth-child(3) { transition-delay: 0.2s; }
.dock-container.visible .app-container> :nth-child(4) { transition-delay: 0.225s; }
.dock-container.visible .app-container> :nth-child(5) { transition-delay: 0.25s; }
.dock-container.visible .app-container> :nth-child(6) { transition-delay: 0.25s; }
.dock-container.visible .app-container> :nth-child(7) { transition-delay: 0.3s; }

/* Suppression des délais quand fully-visible */
.dock-container.visible.fully-visible .dock-item,
.dock-container.visible.fully-visible .dock-separator,
.dock-container.visible.fully-visible .volume-controls,
.dock-container.visible.fully-visible .app-container>* {
  transition-delay: 0s !important;
}

/* Contenu des items */
.dock-item-icon {
  width: 48px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* Indicateur d'élément actif */
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

/* États */
.dock-item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Responsive */
.mobile-only {
  display: flex;
}

.desktop-only {
  display: flex;
}

/* Mobile responsive */
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
  }

  .dock-indicator.visible {
    opacity: 1;
  }

  .dock-indicator.hidden {
    opacity: 0 !important;
    transition: opacity var(--transition-normal) !important;
  }
}

/* iOS */
.ios-app .drag-zone {
  height: var(--space-09);
}

.ios-app .dock-indicator {
  bottom: var(--space-08);
}

.ios-app .dock-container.visible {
  transform: translate(-50%) translateY(-48px) scale(1);
}

/* Desktop */
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

/* Animations de press */
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