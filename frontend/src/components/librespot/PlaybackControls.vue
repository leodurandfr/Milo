<template>
  <div class="controls">
    <button @click="onPrevious" class="control-button previous">
      <Icon name="previous" :size="48" class="icon-secondary" />
    </button>
    <button @click="onPlayPause" class="control-button play-pause">
      <Icon :name="isPlaying ? 'pause' : 'play'" :size="48" class="icon-primary" />
    </button>
    <button @click="onNext" class="control-button next">
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
  transition: background-color 0.2s;
}

.control-button:hover {
  background-color: rgba(255, 255, 255, 0.1);
}

.control-button.play-pause {
  width: 60px;
  height: 60px;
}

/* Couleurs des icônes */
.icon-primary {
  color: var(--color-text);
}

.icon-secondary {
  color: var(--color-text-light);
}
</style>