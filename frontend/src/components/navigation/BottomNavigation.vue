<!-- frontend/src/components/navigation/BottomNavigation.vue - Version OPTIM sans modalStore -->
<template>
  <!-- Zone de drag invisible -->
  <div ref="dragZone" class="drag-zone" :class="{ dragging: isDragging }"></div>

  <!-- Dock de navigation -->
  <nav ref="dockContainer" class="dock-container" :class="{ visible: isVisible, 'fully-visible': isFullyVisible }">
    <!-- Additional Apps - Mobile uniquement -->
    <div v-if="showAdditionalContainer" class="additional-apps-container mobile-only"
      :class="{ visible: showAdditionalApps }">
      <!-- Multiroom -->
      <button @click="() => { resetHideTimer(); moveIndicatorTo(3); openSnapcast(); }"
              @touchstart="addPressEffect"
              @mousedown="addPressEffect"
              class="additional-app-content button-interactive-subtle">
        <AppIcon name="multiroom" :size="32" />
        <div class="app-title heading-2">Multiroom</div>
      </button>

      <!-- Equalizer -->
      <button @click="() => { resetHideTimer(); moveIndicatorTo(4); openEqualizer(); }"
              @touchstart="addPressEffect"
              @mousedown="addPressEffect"
              class="additional-app-content button-interactive-subtle">
        <AppIcon name="equalizer" :size="32" />
        <div class="app-title heading-2">Égaliseur</div>
      </button>
    </div>
    <div ref="dock" class="dock">

      <!-- Volume Controls - Mobile uniquement -->
      <div class="volume-controls mobile-only">
        <button @click="handleVolumeDown" 
                @touchstart="addPressEffect"
                @mousedown="addPressEffect"
                class="volume-btn button-interactive-subtle"
                :disabled="volumeStore.isAdjusting">
          <Icon name="minus" :size="32" />
        </button>
        <button @click="handleVolumeUp" 
                @touchstart="addPressEffect"
                @mousedown="addPressEffect"
                class="volume-btn button-interactive-subtle"
                :disabled="volumeStore.isAdjusting">
          <Icon name="plus" :size="32" />
        </button>
      </div>

      <!-- App Container -->
      <div class="app-container">
        <!-- Spotify -->
        <button ref="dockItem0" @click="() => { resetHideTimer(); moveIndicatorTo(0); changeSource('librespot'); }"
          @touchstart="addPressEffect"
          @mousedown="addPressEffect"
          :disabled="unifiedStore.isTransitioning" class="dock-item button-interactive-subtle">
          <AppIcon name="spotify" size="large" class="dock-item-icon" />
        </button>

        <!-- Bluetooth -->
        <button ref="dockItem1" @click="() => { resetHideTimer(); moveIndicatorTo(1); changeSource('bluetooth'); }"
          @touchstart="addPressEffect"
          @mousedown="addPressEffect"
          :disabled="unifiedStore.isTransitioning" class="dock-item button-interactive-subtle">
          <AppIcon name="bluetooth" size="large" class="dock-item-icon" />
        </button>

        <!-- ROC for Mac -->
        <button ref="dockItem2" @click="() => { resetHideTimer(); moveIndicatorTo(2); changeSource('roc'); }"
          @touchstart="addPressEffect"
          @mousedown="addPressEffect"
          :disabled="unifiedStore.isTransitioning" class="dock-item button-interactive-subtle">
          <AppIcon name="roc" size="large" class="dock-item-icon" />
        </button>

        <!-- Séparateur -->
        <div ref="separator" class="dock-separator"></div>

        <!-- Toggle Additional Apps - Mobile uniquement -->
        <button ref="dockToggle" 
                @click="() => { resetHideTimer(); toggleAdditionalApps(); }"
                @touchstart="addPressEffect"
                @mousedown="addPressEffect"
                class="dock-item toggle-btn mobile-only button-interactive">
          <Icon :name="showAdditionalApps ? 'closeDots' : 'threeDots'" :size="32" class="toggle-icon" />
        </button>

        <!-- Multiroom - Desktop uniquement -->
        <button ref="dockItem3" @click="() => { resetHideTimer(); moveIndicatorTo(3); openSnapcast(); }"
          @touchstart="addPressEffect"
          @mousedown="addPressEffect"
          class="dock-item desktop-only button-interactive-subtle">
          <AppIcon name="multiroom" size="large" class="dock-item-icon" />
        </button>

        <!-- Equalizer - Desktop uniquement -->
        <button ref="dockItem4" @click="() => { resetHideTimer(); moveIndicatorTo(4); openEqualizer(); }"
          @touchstart="addPressEffect"
          @mousedown="addPressEffect"
          class="dock-item desktop-only button-interactive-subtle">
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
import AppIcon from '@/components/ui/AppIcon.vue';
import Icon from '@/components/ui/Icon.vue';

// === ÉMISSIONS VERS APP.VUE ===
const emit = defineEmits(['open-snapcast', 'open-equalizer']);

// Stores
const unifiedStore = useUnifiedAudioStore();
const volumeStore = useVolumeStore();

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
const isFullyVisible = ref(false); // Nouvel état pour gérer les transition-delay
const isDragging = ref(false);
const showAdditionalApps = ref(false);
const showAdditionalContainer = ref(false);

// Variables de drag
let dragStartY = 0;
let dragCurrentY = 0;
const dragThreshold = 30;
let clickTimeout = null;
let hideTimeout = null;

// Computed pour gérer la présence dans le DOM
const shouldShowAdditionalContainer = computed(() => {
  return isVisible.value || showAdditionalApps.value;
});

// === ACTIONS MODALES ===
function openSnapcast() {
  console.log('📻 BottomNavigation: Opening Snapcast modal');
  emit('open-snapcast');
}

function openEqualizer() {
  console.log('🎛️ BottomNavigation: Opening Equalizer modal');
  emit('open-equalizer');
}

// === ADDITIONAL APPS ===
function toggleAdditionalApps() {
  showAdditionalApps.value = !showAdditionalApps.value;
}

// === INDICATEUR ACTIF ===
const indicatorStyle = ref({
  opacity: '0',
  transform: 'translateX(0px)',
  transition: 'all var(--transition-spring)'
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
      transition: 'all var(--transition-spring)'
    };
  });
}

function updateActiveIndicator() {
  if (!isVisible.value) return;

  // OPTIM : Déterminer quel élément est actif (seulement les sources audio)
  let activeIndex = -1;

  if (unifiedStore.currentSource === 'librespot') activeIndex = 0;
  else if (unifiedStore.currentSource === 'bluetooth') activeIndex = 1;
  else if (unifiedStore.currentSource === 'roc') activeIndex = 2;
  // Note: On ne gère plus l'indicateur pour les modales (OPTIM)

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
        transition: 'opacity var(--transition-normal), transform var(--transition-spring)'
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
  // OPTIM : Fermer les modales quand on change de source
  // On n'a plus accès à modalStore, donc on peut soit :
  // 1. Émettre un événement de fermeture
  // 2. Ou laisser App.vue gérer ça automatiquement
  // Pour rester OPTIM, on fait confiance à l'UX que l'utilisateur fermera manuellement
  await unifiedStore.changeSource(source);
}

async function increaseVolume() {
  await volumeStore.increaseVolume();
}

async function decreaseVolume() {
  await volumeStore.decreaseVolume();
}

// === GESTION DES CLICS AVEC EFFET PRESS TEMPORAIRE ===
function addPressEffect(e) {
  const button = e.target.closest('button');
  if (!button || button.disabled) return;
  
  // Ajouter la classe d'effet press
  button.classList.add('is-pressed');
  
  // Retirer automatiquement après 150ms
  setTimeout(() => {
    button.classList.remove('is-pressed');
  }, 150);
}

function handleVolumeUp(e) {
  resetHideTimer();
  increaseVolume();
}

function handleVolumeDown(e) {
  resetHideTimer();
  decreaseVolume();
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
  showAdditionalContainer.value = true; // Ajouter au DOM d'abord
  isVisible.value = true;
  isFullyVisible.value = false; // Reset pour permettre l'animation stagger
  clearHideTimer();
  startHideTimer();

  // Marquer comme fully-visible après la fin de l'animation stagger (0.3s + marge)
  setTimeout(() => {
    isFullyVisible.value = true;
  }, 400);

  setTimeout(() => {
    updateActiveIndicator();
  }, 500);
}

function hideDock() {
  if (!isVisible.value) return;
  
  // Retirer fully-visible pour réactiver les délais pour l'animation de sortie
  isFullyVisible.value = false;
  
  // Déclencher l'animation de disparition
  showAdditionalApps.value = false;
  isVisible.value = false;
  clearHideTimer();
  indicatorStyle.value.opacity = '0';
  
  // Retirer du DOM après l'animation
  setTimeout(() => {
    showAdditionalContainer.value = false;
  }, 300); // Durée de --transition-spring
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


watch(() => unifiedStore.currentSource, updateActiveIndicator);

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
  opacity: 0;
  pointer-events: none;
  transition: all var(--transition-spring);
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

/* Items du dock avec stagger animation */
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

/* Animation staggerée - avec délais par défaut */
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
  transition-delay: 0.225s;
}

/* Toggle mobile (5ème) - masqué en desktop */
.dock-container.visible .app-container > :nth-child(5) {
  transition-delay: 0.25s;
}

/* Multiroom desktop - 6ème enfant global */
.dock-container.visible .app-container > :nth-child(6) {
  transition-delay: 0.25s;
}

/* Equalizer desktop - 7ème enfant global */
.dock-container.visible .app-container > :nth-child(7) {
  transition-delay: 0.3s;
}

/* === SUPPRESSION DES DÉLAIS QUAND FULLY-VISIBLE === */
/* Une fois l'animation terminée, supprimer tous les délais pour des interactions réactives */
.dock-container.visible.fully-visible .dock-item,
.dock-container.visible.fully-visible .dock-separator,
.dock-container.visible.fully-visible .volume-controls,
.dock-container.visible.fully-visible .app-container > * {
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
}

/* Desktop - masquer les éléments mobile */
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

/* === ANIMATIONS DE PRESS GÉNÉRIQUES === */
/* Animation pour les volume buttons - spécifique et isolée */
.volume-btn.is-pressed {
  transform: scale(0.92) !important;
  opacity: 0.8 !important;
  transition-delay: 0s !important; 
}

/* Animation pour les dock-items (app icons) */
.dock-item.is-pressed {
  transform: scale(0.92) !important;
  opacity: 0.8 !important;
  transition-delay: 0s !important; 
}

/* Animation pour les additional-app-content */
.additional-app-content.is-pressed {
  transform: scale(0.97) !important;
  opacity: 0.8 !important;
  transition-delay: 0s !important; /* Override du stagger delay */
}

/* Pas d'animation sur les boutons disabled */
button:disabled.is-pressed {
  transform: none !important;
  opacity: 0.5 !important;
}
</style>