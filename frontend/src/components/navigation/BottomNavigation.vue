<!-- frontend/src/components/navigation/BottomNavigation.vue -->
<template>
  <!-- Zone de drag invisible -->
  <div ref="dragZone" class="drag-zone" :class="{ dragging: isDragging }"></div>



  <!-- Dock de navigation -->
  <nav ref="dockContainer" class="dock-container" :class="{ visible: isVisible }">
    <!-- Additional Apps - Mobile uniquement -->
    <div v-if="showAdditionalApps" class="additional-apps-container mobile-only"
      :class="{ visible: showAdditionalApps }">
      <!-- Multiroom -->
      <button @click="() => { resetHideTimer(); moveIndicatorTo(3); modalStore.openSnapcast(); }"
        class="additional-app-content">
        <AppIcon name="multiroom" :size="32" />
        <div class="app-title heading-2">Multiroom</div>
      </button>

      <!-- Equalizer -->
      <button @click="() => { resetHideTimer(); moveIndicatorTo(4); modalStore.openEqualizer(); }"
        class="additional-app-content">
        <AppIcon name="equalizer" :size="32" />
        <div class="app-title heading-2">Égaliseur</div>
      </button>
    </div>
    <div ref="dock" class="dock">

      <!-- Volume Controls - Mobile uniquement -->
      <div class="volume-controls mobile-only">
        <button @click="() => { resetHideTimer(); decreaseVolume(); }" class="volume-btn"
          :disabled="volumeStore.isAdjusting">
          <Icon name="minus" :size="32" />
        </button>
        <button @click="() => { resetHideTimer(); increaseVolume(); }" class="volume-btn"
          :disabled="volumeStore.isAdjusting">
          <Icon name="plus" :size="32" />
        </button>
      </div>

      <!-- App Container -->
      <div class="app-container">
        <!-- Spotify -->
        <button ref="dockItem0" @click="() => { resetHideTimer(); moveIndicatorTo(0); changeSource('librespot'); }"
          :disabled="unifiedStore.isTransitioning" class="dock-item">
          <AppIcon name="spotify" size="large" class="dock-item-icon" />
        </button>

        <!-- Bluetooth -->
        <button ref="dockItem1" @click="() => { resetHideTimer(); moveIndicatorTo(1); changeSource('bluetooth'); }"
          :disabled="unifiedStore.isTransitioning" class="dock-item">
          <AppIcon name="bluetooth" size="large" class="dock-item-icon" />
        </button>

        <!-- ROC for Mac -->
        <button ref="dockItem2" @click="() => { resetHideTimer(); moveIndicatorTo(2); changeSource('roc'); }"
          :disabled="unifiedStore.isTransitioning" class="dock-item">
          <AppIcon name="roc" size="large" class="dock-item-icon" />
        </button>

        <!-- Séparateur -->
        <div ref="separator" class="dock-separator"></div>

        <!-- Toggle Additional Apps - Mobile uniquement -->
        <button ref="dockToggle" @click="() => { resetHideTimer(); toggleAdditionalApps(); }"
          class="dock-item toggle-btn mobile-only">
          <Icon :name="showAdditionalApps ? 'closeDots' : 'threeDots'" :size="32" class="toggle-icon" />
        </button>

        <!-- Multiroom - Desktop uniquement -->
        <button ref="dockItem3" @click="() => { resetHideTimer(); moveIndicatorTo(3); modalStore.openSnapcast(); }"
          class="dock-item desktop-only">
          <AppIcon name="multiroom" size="large" class="dock-item-icon" />
        </button>

        <!-- Equalizer - Desktop uniquement -->
        <button ref="dockItem4" @click="() => { resetHideTimer(); moveIndicatorTo(4); modalStore.openEqualizer(); }"
          class="dock-item desktop-only">
          <AppIcon name="equalizer" size="large" class="dock-item-icon" />
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
import { useModalStore } from '@/stores/modalStore';
import AppIcon from '@/components/ui/AppIcon.vue';
import Icon from '@/components/ui/Icon.vue';

// Stores
const unifiedStore = useUnifiedAudioStore();
const volumeStore = useVolumeStore();
const modalStore = useModalStore();

// Refs pour le drag et les éléments
const dragZone = ref(null);
const dockContainer = ref(null);
const dock = ref(null);
const activeIndicator = ref(null);
const separator = ref(null);
const dockToggle = ref(null);

// Refs pour les dock-items (pour calculer la position de l'indicateur)
const dockItem0 = ref(null);
const dockItem1 = ref(null);
const dockItem2 = ref(null);
const dockItem3 = ref(null);
const dockItem4 = ref(null);

// État du dock
const isVisible = ref(false);
const isDragging = ref(false);
const showAdditionalApps = ref(false);

// Variables de drag
let dragStartY = 0;
let dragCurrentY = 0;
const dragThreshold = 30;
let clickTimeout = null;
let hideTimeout = null;

// === ADDITIONAL APPS ===
function toggleAdditionalApps() {
  showAdditionalApps.value = !showAdditionalApps.value;
}

// === INDICATEUR ACTIF ===
const indicatorStyle = ref({
  opacity: '0',
  transform: 'translateX(0px)',
  transition: 'all var(--transition-normal)'
});

// Bouger l'indicateur immédiatement vers un index donné
function moveIndicatorTo(index) {
  if (!isVisible.value) return;

  nextTick(() => {
    const dockItems = [
      dockItem0.value, dockItem1.value, dockItem2.value,
      dockItem3.value, dockItem4.value
    ];

    const targetItem = dockItems[index];
    if (!targetItem || !dock.value) return;

    const dockRect = dock.value.getBoundingClientRect();
    const itemRect = targetItem.getBoundingClientRect();

    // Centrer parfaitement l'indicateur 4x4px sous l'item
    const itemCenterX = itemRect.left - dockRect.left + (itemRect.width / 2);
    const offsetX = itemCenterX - 2; // -2 pour centrer le bloc de 4px

    indicatorStyle.value = {
      opacity: '1',
      transform: `translateX(${offsetX}px)`,
      transition: 'all var(--transition-normal)'
    };
  });
}

function updateActiveIndicator() {
  if (!isVisible.value) return;

  // Déterminer quel élément est actif
  let activeIndex = -1;

  if (unifiedStore.currentSource === 'librespot') activeIndex = 0;
  else if (unifiedStore.currentSource === 'bluetooth') activeIndex = 1;
  else if (unifiedStore.currentSource === 'roc') activeIndex = 2;
  else if (modalStore.isSnapcastOpen) activeIndex = 3;
  else if (modalStore.isEqualizerOpen) activeIndex = 4;

  if (activeIndex === -1) {
    indicatorStyle.value.opacity = '0';
    return;
  }

  nextTick(() => {
    const dockItems = [
      dockItem0.value, dockItem1.value, dockItem2.value,
      dockItem3.value, dockItem4.value
    ];

    const targetItem = dockItems[activeIndex];
    if (!targetItem || !dock.value) return;

    const dockRect = dock.value.getBoundingClientRect();
    const itemRect = targetItem.getBoundingClientRect();

    const itemCenterX = itemRect.left - dockRect.left + (itemRect.width / 2);
    const offsetX = itemCenterX - 2;

    // Positionner immédiatement sans transition pour le transform
    indicatorStyle.value = {
      opacity: '0', // Commencer invisible
      transform: `translateX(${offsetX}px)`,
      transition: 'none'
    };

    // Animer l'apparition après un court délai
    setTimeout(() => {
      indicatorStyle.value = {
        opacity: '1',
        transform: `translateX(${offsetX}px)`,
        transition: 'opacity var(--transition-normal), transform var(--transition-normal)'
      };
    }, 50);
  });
}

// === TIMER AUTO-MASQUAGE ===
function startHideTimer() {
  clearTimeout(hideTimeout);
  hideTimeout = setTimeout(() => {
    hideDock();
  }, 10000);
}

function clearHideTimer() {
  clearTimeout(hideTimeout);
}

function resetHideTimer() {
  if (isVisible.value) {
    startHideTimer();
  }
}

async function changeSource(source) {
  modalStore.closeAll();
  await unifiedStore.changeSource(source);
}

async function increaseVolume() {
  await volumeStore.increaseVolume();
}

async function decreaseVolume() {
  await volumeStore.decreaseVolume();
}

// === LOGIQUE DRAG ===
function getEventY(e) {
  return e.type.includes('touch')
    ? (e.touches[0]?.clientY || e.changedTouches[0]?.clientY)
    : e.clientY;
}

function onDragStart(e) {
  isDragging.value = true;
  dragStartY = getEventY(e);
  dragCurrentY = dragStartY;
}

function onDragMove(e) {
  if (!isDragging.value) return;

  dragCurrentY = getEventY(e);
  const deltaY = dragStartY - dragCurrentY;
  const absDelta = Math.abs(deltaY);

  if (absDelta >= dragThreshold) {
    if (deltaY > 0 && !isVisible.value) {
      showDock();
      isDragging.value = false;
    } else if (deltaY < 0 && isVisible.value) {
      hideDock();
      isDragging.value = false;
    }
  }
}

function onDragEnd() {
  clearTimeout(clickTimeout);
  isDragging.value = false;

  if (!isDragging.value) {
    resetHideTimer();
  }
}

function showDock() {
  if (isVisible.value) return;
  isVisible.value = true;
  clearHideTimer();
  startHideTimer();

  setTimeout(() => {
    updateActiveIndicator();
  }, 500);
}

function hideDock() {
  if (!isVisible.value) return;
  isVisible.value = false;
  showAdditionalApps.value = false; // Fermer aussi les apps additionnelles
  clearHideTimer();
  indicatorStyle.value.opacity = '0';
}

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
}

function removeDragEvents() {
  const zone = dragZone.value;
  const dockEl = dock.value;
  if (!zone) return;

  zone.removeEventListener('mousedown', onDragStart);
  zone.removeEventListener('touchstart', onDragStart);

  if (dockEl) {
    dockEl.removeEventListener('mousedown', onDragStart);
    dockEl.removeEventListener('touchstart', onDragStart);
  }

  document.removeEventListener('mousemove', onDragMove);
  document.removeEventListener('mouseup', onDragEnd);
  document.removeEventListener('touchmove', onDragMove);
  document.removeEventListener('touchend', onDragEnd);
}

// === WATCHERS ===
watch(() => unifiedStore.currentSource, updateActiveIndicator);
watch(() => modalStore.isSnapcastOpen, updateActiveIndicator);
watch(() => modalStore.isEqualizerOpen, updateActiveIndicator);

onMounted(() => {
  setupDragEvents();
});

onUnmounted(() => {
  removeDragEvents();
  clearHideTimer();
  clearTimeout(clickTimeout);
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
  transform: translateX(-50%) translateY(20px);
  z-index: 998;
  border-radius: var(--radius-06);
  padding: var(--space-04);
  background: var(--color-background-glass);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.additional-apps-container.visible {
  opacity: 1;
  transform: translateX(-50%) translateY(0);
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
  transition: all var(--transition-fast);
  opacity: 0;
  transform: translateY(20px);
}

.additional-apps-container.visible .additional-app-content {
  opacity: 1;
  transform: translateY(0);
}

.additional-apps-container.visible .additional-app-content:first-child {
  transition-delay: 0.1s;
}

.additional-apps-container.visible .additional-app-content:nth-child(2) {
  transition-delay: 0.2s;
}

.additional-app-content:hover {
  background: var(--color-background-neutral-12);
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
  transition: transform var(--spring-transition);
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
  transition: all var(--spring-transition);
}

.volume-btn {
  flex: 1;
  background: var(--color-background-neutral-64);
  border-radius: var(--radius-04);
  cursor: pointer;
  color: var(--color-text-secondary);
  padding: var(--space-02);
  transition: all var(--transition-normal);
}

.volume-btn:hover {
  background: var(--color-background-neutral);
  color: var(--color-text);
}

.volume-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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
  background: var(--color-background-neutral-32);
  border-radius: var(--radius-full);
  opacity: 0;
  transform: translateY(20px) scale(0.8);
  transition: all var(--spring-transition);
}

/* Items du dock avec stagger animation */
.dock-item {
  cursor: pointer;
  opacity: 0;
  transform: translateY(20px) scale(0.8);
  transition: all var(--spring-transition);
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

/* Animation staggerée - dans l'ordre demandé */
.dock-container.visible .dock-item,
.dock-container.visible .dock-separator,
.dock-container.visible .volume-controls {
  opacity: 1;
  transform: translateY(0) scale(1);
}

/* Volume Controls - apparaissent en même temps que les premiers */
.dock-container.visible .volume-controls {
  transition-delay: 0.1s;
}

/* Ciblage par position globale dans .app-container */
/* Spotify - 1er enfant global */
.dock-container.visible .app-container > :nth-child(1) {
  transition-delay: 0.1s;
}

/* Bluetooth - 2ème enfant global */
.dock-container.visible .app-container > :nth-child(2) {
  transition-delay: 0.15s;
}

/* ROC - 3ème enfant global */
.dock-container.visible .app-container > :nth-child(3) {
  transition-delay: 0.2s;
}

/* Séparateur - 4ème enfant global */
.dock-container.visible .app-container > :nth-child(4) {
  transition-delay: 0.25s;
}

/* Toggle mobile (5ème) - masqué en desktop */
.dock-container.visible .app-container > :nth-child(5) {
  transition-delay: 0.3s;
}

/* Multiroom desktop - 6ème enfant global */
.dock-container.visible .app-container > :nth-child(6) {
  transition-delay: 0.3s;
}

/* Equalizer desktop - 7ème enfant global */
.dock-container.visible .app-container > :nth-child(7) {
  transition-delay: 0.35s;
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
  transition: opacity var(--transition-slow), transform var(--transition-slow);
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
    height: 12%;
  }

  .desktop-only {
    display: none;
  }
}

/* Desktop - masquer les éléments mobile */
@media not (max-aspect-ratio: 4/3) {
  .mobile-only {
    display: none;
  }

  .dock {
    flex-direction: row;
  }
}
</style>