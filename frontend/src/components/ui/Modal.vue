<!-- frontend/src/components/ui/Modal.vue -->
<template>
  <div v-if="isOpen" class="modal-overlay" @click.self="handleOverlayClick">
    <div class="modal-container" :class="sizeClass">
      <!-- En-tête -->
      <div class="modal-header">
        <div class="header-left">
          <button v-if="showBackButton" @click="goBack" class="back-btn" aria-label="Retour">←</button>
          <h2 v-if="title">{{ title }}</h2>
        </div>
        <button @click="close" class="close-btn" aria-label="Fermer">✕</button>
      </div>

      <!-- Contenu -->
      <div class="modal-content">
        <slot></slot>
      </div>

      <!-- Actions optionnelles -->
      <div v-if="$slots.actions" class="modal-actions">
        <slot name="actions"></slot>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted } from 'vue';

const props = defineProps({
  isOpen: {
    type: Boolean,
    required: true
  },
  title: {
    type: String,
    default: ''
  },
  size: {
    type: String,
    default: 'medium', // 'small', 'medium', 'large', 'fullscreen'
    validator: (value) => ['small', 'medium', 'large', 'fullscreen'].includes(value)
  },
  closeOnOverlay: {
    type: Boolean,
    default: true
  },
  showBackButton: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['close', 'back']);

const sizeClass = computed(() => `modal-${props.size}`);

function close() {
  emit('close');
}

function goBack() {
  emit('back');
}

function handleOverlayClick() {
  if (props.closeOnOverlay) {
    close();
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
  document.body.style.overflow = ''; // Restaurer le scroll
});

// Watcher pour le scroll
import { watch } from 'vue';
watch(() => props.isOpen, (newValue) => {
  toggleBodyScroll(newValue);
});
</script>

<style scoped>
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 20px;
}

.modal-container {
  background: white;
  border-radius: 8px;
  border: 1px solid #ddd;
  max-height: 90vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  box-shadow: 0 10px 25px rgba(0, 0, 0, 0.3);
}

/* Tailles de modales */
.modal-small {
  width: 100%;
  max-width: 400px;
}

.modal-medium {
  width: 100%;
  max-width: 600px;
}

.modal-large {
  width: 100%;
  max-width: 800px;
}

.modal-fullscreen {
  width: 95vw;
  height: 95vh;
  max-width: none;
  max-height: none;
}

/* En-tête */
.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px;
  border-bottom: 1px solid #eee;
  background: #f8f9fa;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
}

.modal-header h2 {
  margin: 0;
  font-size: 20px;
  color: #333;
}

.back-btn {
  background: none;
  border: none;
  font-size: 24px;
  cursor: pointer;
  padding: 4px 8px;
  color: #666;
  border-radius: 4px;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: bold;
}

.back-btn:hover {
  background: #e9ecef;
  color: #333;
}

.close-btn {
  background: none;
  border: none;
  font-size: 24px;
  cursor: pointer;
  padding: 4px;
  color: #666;
  border-radius: 50%;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.close-btn:hover {
  background: #e9ecef;
  color: #333;
}

/* Contenu */
.modal-content {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

/* Actions */
.modal-actions {
  padding: 20px;
  border-top: 1px solid #eee;
  background: #f8f9fa;
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

/* Responsive */
@media (max-width: 600px) {
  .modal-overlay {
    padding: 10px;
  }
  
  .modal-container {
    border-radius: 4px;
  }
  
  .modal-header {
    padding: 16px;
  }
  
  .modal-content {
    padding: 16px;
  }
  
  .modal-actions {
    padding: 16px;
  }
}
</style>