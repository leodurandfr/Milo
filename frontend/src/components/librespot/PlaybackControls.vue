<template>
  <div class="controls">
    <div @click="onPrevious" class="control-button previous">
      <SvgIcon name="previous" :size="48" class="icon-secondary" />
    </div>
    <div @click="onPlayPause" class="control-button play-pause">
      <SvgIcon :name="isPlaying ? 'pause' : 'play'" :size="48" class="icon-primary" />
    </div>
    <div @click="onNext" class="control-button next">
      <SvgIcon name="next" :size="48" class="icon-secondary" />
    </div>
  </div>
</template>

<script setup>
import SvgIcon from '@/components/ui/SvgIcon.vue';

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
  const button = e.currentTarget;
  if (!button || button.disabled) return;

  button.classList.add('is-pressed');
  setTimeout(() => {
    button.classList.remove('is-pressed');
  }, 150);
}

function onPlayPause(e) {
  addPressEffect(e);
  emit('play-pause');
}

function onPrevious(e) {
  addPressEffect(e);
  emit('previous');
}

function onNext(e) {
  addPressEffect(e);
  emit('next');
}
</script>

<style scoped>
.controls {
  background: var(--color-background);
  border-radius: var(--radius-07);
  display: flex;
  justify-content: space-evenly;
  align-items: center;
  padding: var(--space-01) var(--space-04);
}

.control-button {
  background: none;
  border: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 80px;
  height: 80px;
  border-radius: 50%;
  transition: background-color 0.2s, transform var(--transition-spring), opacity 0.2s;
}

.control-button:hover {
  background-color: rgba(255, 255, 255, 0.1);
}

.control-button.play-pause {
  width: 90px;
  height: 90px;
}

.control-button:active {
  transform: scale(0.8);
  opacity: 0.5;
  transition: transform 0.1s ease, opacity 0.1s ease;
}

.icon-primary {
  color: var(--color-text);
  pointer-events: none;
}

.icon-secondary {
  color: var(--color-text-light);
  pointer-events: none;
}

@media (max-aspect-ratio: 4/3) {}
</style>