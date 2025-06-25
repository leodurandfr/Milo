<!-- frontend/src/components/ui/Modal.vue - Version avec header conditionnel -->
<template>
  <div v-if="isOpen" class="modal-overlay" @click.self="handleOverlayClick">
    <div class="modal-container">
      <!-- Bouton close flottant à l'extérieur -->
      <button @click="close" class="close-btn-floating" aria-label="Fermer">✕</button>

      <!-- Header seulement pour les sous-écrans (avec back) -->
      <div v-if="showBackButton" class="modal-header">
        <button @click="goBack" class="back-btn" aria-label="Retour">←</button>
        <h2 v-if="title" class="modal-title">{{ title }}</h2>
      </div>

      <!-- Contenu -->
      <div class="modal-content">
        <slot></slot>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted } from 'vue';

const props = defineProps({
  isOpen: {
    type: Boolean,
    required: true
  },
  title: {
    type: String,
    default: ''
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
  document.body.style.overflow = '';
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
  backdrop-filter: blur(10px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 20px;
}

.modal-container {
  position: relative;
  background: rgba(255, 255, 255, 0.241);
  border-radius: 32px;
  width: 100%;
  max-width: 700px;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 10px 25px rgba(0, 0, 0, 0.3);
}

/* Bouton close flottant à l'extérieur */
.close-btn-floating {
  position: absolute;
  right: calc(-16px - 48px);
  background: white;
  border: 2px solid #ddd;
  border-radius: 50%;
  width: 40px;
  height: 40px;
  font-size: 20px;
  cursor: pointer;
  color: #666;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
  z-index: 1001;
}

.close-btn-floating:hover {
  background: #f5f5f5;
  color: #333;
  border-color: #999;
  transform: scale(1.05);
}

/* Header conditionnel pour sous-écrans */
.modal-header {
  display: flex;
  align-items: center;
  gap: 12px;
  border-radius: 16px;
  padding: 16px;
  margin: 16px 16px 0 16px;
  background: #f8f9fa;
}

.back-btn {
  background: none;
  border: none;
  font-size: 20px;
  cursor: pointer;
  padding: 8px;
  color: #666;
  border-radius: 4px;
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
  flex-shrink: 0;
}

.back-btn:hover {
  background: #e9ecef;
  color: #333;
}

.modal-title {
  margin: 0;
  font-size: 18px;
  color: #333;
  flex: 1;
}

/* Contenu */
.modal-content {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}

/* Responsive */
@media (max-width: 600px) {
  .modal-overlay {
    padding: 15px;
  }

  .modal-content {
    padding: 16px;
  }

  .modal-header {
    padding: 12px 16px;
  }

  .modal-title {
    font-size: 16px;
  }

  .close-btn-floating {
    top: -15px;
    right: -15px;
    width: 36px;
    height: 36px;
    font-size: 18px;
  }
}
</style>