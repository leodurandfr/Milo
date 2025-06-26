<!-- frontend/src/components/navigation/BottomNavigation.vue -->
<template>
  <!-- Zone de drag invisible -->
  <div ref="dragZone" class="drag-zone" :class="{ dragging: isDragging }"></div>

  <!-- Dock de navigation -->
  <nav ref="dockContainer" class="dock-container" :class="{ visible: isVisible }">
    <div ref="dock" class="dock">
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

      <!-- Multiroom -->
      <button ref="dockItem3" @click="() => { resetHideTimer(); moveIndicatorTo(3); modalStore.openSnapcast(); }"
        class="dock-item">
        <AppIcon name="multiroom" size="large" class="dock-item-icon" />
      </button>

      <!-- Equalizer -->
      <button ref="dockItem4" @click="() => { resetHideTimer(); moveIndicatorTo(4); modalStore.openEqualizer(); }"
        class="dock-item">
        <AppIcon name="equalizer" size="large" class="dock-item-icon" />
      </button>

      <!-- Volume - (Mobile uniquement) -->
      <button ref="dockItem5" @click="() => { resetHideTimer(); decreaseVolume(); }"
        class="dock-item volume-btn mobile-only" :disabled="volumeStore.isAdjusting">
        <div class="dock-item-icon text-icon">-</div>
      </button>

      <!-- Volume + (Mobile uniquement) -->
      <button ref="dockItem6" @click="() => { resetHideTimer(); increaseVolume(); }"
        class="dock-item volume-btn mobile-only" :disabled="volumeStore.isAdjusting">
        <div class="dock-item-icon text-icon">+</div>
      </button>

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

// Refs pour les dock-items (pour calculer la position de l'indicateur)
const dockItem0 = ref(null);
const dockItem1 = ref(null);
const dockItem2 = ref(null);
const dockItem3 = ref(null);
const dockItem4 = ref(null);
const dockItem5 = ref(null);
const dockItem6 = ref(null);

// État du dock
const isVisible = ref(false);
const isDragging = ref(false);

// Variables de drag
let dragStartY = 0;
let dragCurrentY = 0;
const dragThreshold = 30;
let clickTimeout = null;
let hideTimeout = null;

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
      dockItem3.value, dockItem4.value, dockItem5.value, dockItem6.value
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
      dockItem3.value, dockItem4.value, dockItem5.value, dockItem6.value
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

/* Dock container avec animation spring */
.dock-container {
  position: fixed;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%) translateY(120px) scale(0.85);
  z-index: 1000;
  transition: transform 0.725s linear(0.000, 0.106 2.0%, 0.219 4.0%, 0.335 6.0%, 0.451 8.0%, 0.561 10.0%,
      0.665 12.0%, 0.760 14.0%, 0.845 16.0%, 0.919 18.0%, 0.982 20.0%,
      1.034 22.0%, 1.076 24.0%, 1.108 26.0%, 1.131 28.0%, 1.145 30.0%,
      1.153 32.0%, 1.155 34.0%, 1.152 36.0%, 1.144 38.0%, 1.134 40.0%,
      1.122 42.0%, 1.108 44.0%, 1.093 46.0%, 1.079 48.0%, 1.064 50.0%,
      1.051 52.0%, 1.038 54.0%, 1.026 56.0%, 1.016 58.0%, 1.007 60.0%,
      1.000 62.0%, 0.994 64.0%, 0.989 66.0%, 0.985 68.0%, 0.983 70.0%,
      0.981 72.0%, 0.981 74.0%, 0.981 76.0%, 0.981 78.0%, 0.982 80.0%,
      0.984 82.0%, 0.985 84.0%, 0.987 86.0%, 0.989 88.0%, 0.991 90.0%,
      0.992 92.0%, 0.994 94.0%, 0.996 96.0%, 0.997 98.0%, 0.998 100.0%);
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
  /* Épaisseur de la "bordure" */
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

/* Séparateur */
.dock-separator {
  width: 2px;
  height: var(--space-07);
  background: var(--color-background-glass);
  border-radius: var(--radius-full);
  opacity: 0;
  transform: translateY(20px) scale(0.8);
  transition: all 0.725s linear(0.000, 0.106 2.0%, 0.219 4.0%, 0.335 6.0%, 0.451 8.0%, 0.561 10.0%,
      0.665 12.0%, 0.760 14.0%, 0.845 16.0%, 0.919 18.0%, 0.982 20.0%,
      1.034 22.0%, 1.076 24.0%, 1.108 26.0%, 1.131 28.0%, 1.145 30.0%,
      1.153 32.0%, 1.155 34.0%, 1.152 36.0%, 1.144 38.0%, 1.134 40.0%,
      1.122 42.0%, 1.108 44.0%, 1.093 46.0%, 1.079 48.0%, 1.064 50.0%,
      1.051 52.0%, 1.038 54.0%, 1.026 56.0%, 1.016 58.0%, 1.007 60.0%,
      1.000 62.0%, 0.994 64.0%, 0.989 66.0%, 0.985 68.0%, 0.983 70.0%,
      0.981 72.0%, 0.981 74.0%, 0.981 76.0%, 0.981 78.0%, 0.982 80.0%,
      0.984 82.0%, 0.985 84.0%, 0.987 86.0%, 0.989 88.0%, 0.991 90.0%,
      0.992 92.0%, 0.994 94.0%, 0.996 96.0%, 0.997 98.0%, 0.998 100.0%);
}

/* Items du dock avec stagger animation */
.dock-item {
  cursor: pointer;
  opacity: 0;
  transform: translateY(20px) scale(0.8);
  transition: all 0.725s linear(0.000, 0.106 2.0%, 0.219 4.0%, 0.335 6.0%, 0.451 8.0%, 0.561 10.0%,
      0.665 12.0%, 0.760 14.0%, 0.845 16.0%, 0.919 18.0%, 0.982 20.0%,
      1.034 22.0%, 1.076 24.0%, 1.108 26.0%, 1.131 28.0%, 1.145 30.0%,
      1.153 32.0%, 1.155 34.0%, 1.152 36.0%, 1.144 38.0%, 1.134 40.0%,
      1.122 42.0%, 1.108 44.0%, 1.093 46.0%, 1.079 48.0%, 1.064 50.0%,
      1.051 52.0%, 1.038 54.0%, 1.026 56.0%, 1.016 58.0%, 1.007 60.0%,
      1.000 62.0%, 0.994 64.0%, 0.989 66.0%, 0.985 68.0%, 0.983 70.0%,
      0.981 72.0%, 0.981 74.0%, 0.981 76.0%, 0.981 78.0%, 0.982 80.0%,
      0.984 82.0%, 0.985 84.0%, 0.987 86.0%, 0.989 88.0%, 0.991 90.0%,
      0.992 92.0%, 0.994 94.0%, 0.996 96.0%, 0.997 98.0%, 0.998 100.0%);
}

/* Animation staggerée - quand visible (incluant le séparateur) */
.dock-container.visible .dock-item,
.dock-container.visible .dock-separator {
  opacity: 1;
  transform: translateY(0) scale(1);
}

.dock-container.visible .dock-item:nth-child(1),
.dock-container.visible .dock-separator:nth-child(1) {
  transition-delay: 0.1s;
}

.dock-container.visible .dock-item:nth-child(2),
.dock-container.visible .dock-separator:nth-child(2) {
  transition-delay: 0.15s;
}

.dock-container.visible .dock-item:nth-child(3),
.dock-container.visible .dock-separator:nth-child(3) {
  transition-delay: 0.2s;
}

.dock-container.visible .dock-item:nth-child(4),
.dock-container.visible .dock-separator:nth-child(4) {
  transition-delay: 0.25s;
}

.dock-container.visible .dock-item:nth-child(5),
.dock-container.visible .dock-separator:nth-child(5) {
  transition-delay: 0.3s;
}

.dock-container.visible .dock-item:nth-child(6),
.dock-container.visible .dock-separator:nth-child(6) {
  transition-delay: 0.35s;
}

.dock-container.visible .dock-item:nth-child(7),
.dock-container.visible .dock-separator:nth-child(7) {
  transition-delay: 0.4s;
}

.dock-container.visible .dock-item:nth-child(8),
.dock-container.visible .dock-separator:nth-child(8) {
  transition-delay: 0.45s;
}

/* Contenu des items */
.dock-item-icon {
  width: 48px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.text-icon {
  background: var(--color-background-neutral);
  border-radius: var(--radius-02);
  font-family: 'Space Mono Regular';
  font-size: var(--font-size-h2);
  font-weight: 600;
  color: var(--color-text);
}

/* Indicateur d'élément actif */
.active-indicator {
  position: absolute;
  bottom: 6px;
  left: 0;
  width: 6px;
  height: 4px;
  background: var(--color-text-secondary);
  border-radius: var(--radius-full);
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--transition-normal), transform var(--transition-normal);
}

/* États */
.dock-item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Boutons volume - masqués en desktop */
.mobile-only {
  display: flex;
}

/* Mobile responsive */
@media (max-aspect-ratio: 4/3) {
  .drag-zone {
    height: 80px;

  }

  .dock {
    gap: var(--space-01);
    flex-wrap: nowrap;
    overflow-x: auto;
  }





  /* Stagger plus rapide sur mobile */
  .dock-container.visible .dock-item:nth-child(1),
  .dock-container.visible .dock-separator:nth-child(1) {
    transition-delay: 0.05s;
  }

  .dock-container.visible .dock-item:nth-child(2),
  .dock-container.visible .dock-separator:nth-child(2) {
    transition-delay: 0.1s;
  }

  .dock-container.visible .dock-item:nth-child(3),
  .dock-container.visible .dock-separator:nth-child(3) {
    transition-delay: 0.15s;
  }

  .dock-container.visible .dock-item:nth-child(4),
  .dock-container.visible .dock-separator:nth-child(4) {
    transition-delay: 0.2s;
  }

  .dock-container.visible .dock-item:nth-child(5),
  .dock-container.visible .dock-separator:nth-child(5) {
    transition-delay: 0.25s;
  }

  .dock-container.visible .dock-item:nth-child(6),
  .dock-container.visible .dock-separator:nth-child(6) {
    transition-delay: 0.3s;
  }

  .dock-container.visible .dock-item:nth-child(7),
  .dock-container.visible .dock-separator:nth-child(7) {
    transition-delay: 0.35s;
  }

  .dock-container.visible .dock-item:nth-child(8),
  .dock-container.visible .dock-separator:nth-child(8) {
    transition-delay: 0.4s;
  }
}

/* Desktop - masquer les boutons volume */
@media not (max-aspect-ratio: 4/3) {
  .mobile-only {
    display: none;
  }
}
</style>