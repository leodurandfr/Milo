<template>
  <div class="controls">
    <button 
      @click="onPrevious" 
      @mousedown="addPressEffect"
      @touchstart="addPressEffect"
      class="control-button previous"
    >
      <Icon name="previous" :size="48" class="icon-secondary" />
    </button>
    <button 
      @click="onPlayPause" 
      @mousedown="addPressEffect"
      @touchstart="addPressEffect"
      class="control-button play-pause"
    >
      <Icon :name="isPlaying ? 'pause' : 'play'" :size="48" class="icon-primary" />
    </button>
    <button 
      @click="onNext" 
      @mousedown="addPressEffect"
      @touchstart="addPressEffect"
      class="control-button next"
    >
      <Icon name="next" :size="48" class="icon-secondary" />
    </button>
  </div>
</template>

<script setup>
import Icon from '@/components/ui/Icon.vue';

const props = defineProps({
  isPlaying: {
    type: Boolean,
    default: false
  },
  isLoading: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['play-pause', 'previous', 'next']);

function addPressEffect(e) {
  const button = e.target.closest('button');
  if (!button || button.disabled) return;

  button.classList.add('is-pressed');
  setTimeout(() => {
    button.classList.remove('is-pressed');
  }, 150);
}

function onPlayPause() {
  emit('play-pause');
}

function onPrevious() {
  emit('previous');
}

function onNext() {
  emit('next');
}
</script>

<style scoped>
.controls {
  background: var(--color-background);
  border-radius: var(--radius-06);
  display: flex;
  justify-content: space-evenly;
  align-items: center;
  padding: var(--space-04);
}

.control-button {
  background: none;
  border: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 50px;
  height: 50px;
  border-radius: 50%;
  transition: background-color 0.2s, transform var(--transition-spring), opacity 0.2s;
}

.control-button:hover {
  background-color: rgba(255, 255, 255, 0.1);
}

.control-button.play-pause {
  width: 60px;
  height: 60px;
}

/* Animation de press pour les boutons de contrôle */
.control-button.is-pressed {
  transform: scale(0.8) !important;
  opacity: 0.5 !important;
  transition-delay: 0s !important;
}

.control-button:disabled.is-pressed {
  transform: none !important;
  opacity: 0.5 !important;
}

/* Couleurs des icônes */
.icon-primary {
  color: var(--color-text);
}

.icon-secondary {
  color: var(--color-text-light);
}
</style>