<!-- frontend/src/components/ui/Modal.vue -->
<template>
  <div v-if="isOpen" class="modal-overlay" :class="{ 'fixed-height': heightMode === 'fixed' }"
    @click.self="handleOverlayClick">
    <div class="modal-container" :class="{ 'fixed-height': heightMode === 'fixed' }">
      <!-- Bouton close avec nouveau composant -->
      <IconButtonFloating 
        class="close-btn-position"
        icon-name="close" 
        aria-label="Fermer"
        @click="close" 
      />

      <!-- Contenu -->
      <div 
        ref="modalContent"
        class="modal-content"
        @pointerdown="handlePointerDown"
        @pointermove="handlePointerMove"
        @pointerup="handlePointerUp"
        @pointercancel="handlePointerUp"
      >
        <slot></slot>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue';
import IconButtonFloating from './IconButtonFloating.vue';

const props = defineProps({
  isOpen: {
    type: Boolean,
    required: true
  },
  closeOnOverlay: {
    type: Boolean,
    default: true
  },
  heightMode: {
    type: String,
    default: 'auto', // 'auto' pour multiroom, 'fixed' pour equalizer
    validator: (value) => ['auto', 'fixed'].includes(value)
  }
});

const emit = defineEmits(['close']);

// Référence au contenu de la modal
const modalContent = ref(null);

// Variables pour le pointer scroll
let isDragging = false;
let startY = 0;
let startScrollTop = 0;
let pointerId = null;
let hasMoved = false;

function close() {
  emit('close');
}

function handleOverlayClick() {
  if (props.closeOnOverlay) {
    close();
  }
}

// Gestion du pointer scroll
function handlePointerDown(event) {
  if (!modalContent.value) return;
  
  // Exclure les sliders et autres contrôles interactifs
  const isSlider = event.target.closest('input[type="range"]');
  const isButton = event.target.closest('button');
  const isInput = event.target.closest('input, select, textarea');
  
  if (isSlider || isButton || isInput) {
    return; // Laisser ces éléments gérer leurs propres événements
  }
  
  isDragging = true;
  hasMoved = false;
  pointerId = event.pointerId;
  startY = event.clientY;
  startScrollTop = modalContent.value.scrollTop;
}

function handlePointerMove(event) {
  if (!isDragging || event.pointerId !== pointerId || !modalContent.value) return;
  
  const deltaY = Math.abs(startY - event.clientY);
  
  // Seuil de mouvement pour distinguer clic vs drag
  if (deltaY > 5) {
    hasMoved = true;
    
    // Capturer seulement quand on commence vraiment à dragger
    if (!modalContent.value.hasPointerCapture(event.pointerId)) {
      modalContent.value.setPointerCapture(event.pointerId);
    }
    
    event.preventDefault();
    
    const scrollDelta = startY - event.clientY;
    modalContent.value.scrollTop = startScrollTop + scrollDelta;
  }
}

function handlePointerUp(event) {
  if (event.pointerId === pointerId) {
    isDragging = false;
    pointerId = null;
    hasMoved = false;
    
    if (modalContent.value && modalContent.value.hasPointerCapture(event.pointerId)) {
      modalContent.value.releasePointerCapture(event.pointerId);
    }
  }
}

// Gestion de l'échappement
function handleKeydown(event) {
  if (event.key === 'Escape' && props.isOpen) {
    close();
  }
}

// Bloquer le scroll du body quand modal ouverte
function toggleBodyScroll(isOpen) {
  if (isOpen) {
    document.body.style.overflow = 'hidden';
  } else {
    document.body.style.overflow = '';
  }
}

onMounted(() => {
  document.addEventListener('keydown', handleKeydown);
});

onUnmounted(() => {
  document.removeEventListener('keydown', handleKeydown);
  document.body.style.overflow = '';
});

// Watcher pour le scroll
watch(() => props.isOpen, (newValue) => {
  toggleBodyScroll(newValue);
});
</script>

<style scoped>
::-webkit-scrollbar {
    display: none;
}
/* Overlay - comportement par défaut (auto) */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: var(--color-background-neutral-50);
  backdrop-filter: blur(32px);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  z-index: 1000;
  padding: 48px var(--space-04) var(--space-07) var(--space-04);
}

/* Overlay mode fixed (equalizer) */
.modal-overlay.fixed-height {
  align-items: center;
}

/* Container - comportement par défaut (auto) */
.modal-container {
  position: relative;
  background: var(--color-background-glass);
  border-radius: var(--radius-06);
  width: 100%;
  max-width: 700px;
  max-height: 100%;
  display: flex;
  flex-direction: column;
}

.modal-container::before {
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

/* Container mode fixed (equalizer) */
.modal-container.fixed-height {
  height: 100%;
}

/* Positionnement du bouton close */
.close-btn-position {
  position: absolute;
  right: calc(-32px - 48px);
  top: 0;
}

/* Contenu - comportement par défaut (auto) */
.modal-content {
  overflow-y: auto;
  padding: var(--space-04);
  display: flex;
  flex-direction: column;
  min-height: 0;
  border-radius: var(--radius-06);
  /* Configuration pour PointerEvents - permet le scroll vertical seulement */
  touch-action: pan-y;
}

/* Contenu mode fixed (equalizer) */
.modal-container.fixed-height .modal-content {
  flex: 1;
  height: 100%;
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  ::-webkit-scrollbar {
    display: none;
}
  .close-btn-position {
    position: fixed;
    top: var(--space-05);
    left: 50%;
    transform: translateX(-50%);
    right: auto;
  }

  .modal-overlay,
  .modal-overlay.fixed-height {
    align-items: flex-start;
    padding: 80px var(--space-02) var(--space-02) var(--space-02);
  }

  .modal-container.fixed-height {
    height: min-content;
  }
}
</style>