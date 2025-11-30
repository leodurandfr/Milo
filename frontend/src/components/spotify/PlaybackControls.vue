<template>
  <div class="controls">
    <div @click="onPrevious" class="control-button previous">
      <SvgIcon name="previous" responsive class="icon-secondary" />
    </div>
    <div @click="onPlayPause" class="control-button play-pause">
      <SvgIcon :name="isPlaying ? 'pause' : 'play'" responsive class="icon-primary" />
    </div>
    <div @click="onNext" class="control-button next">
      <SvgIcon name="next" responsive class="icon-secondary" />
    </div>
  </div>
</template>

<script setup>
import SvgIcon from '@/components/ui/SvgIcon.vue';

defineProps({
  isPlaying: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['play-pause', 'previous', 'next']);

const onPlayPause = () => emit('play-pause');
const onPrevious = () => emit('previous');
const onNext = () => emit('next');
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

.control-button :deep(.svg-responsive) {
  width: 48px;
  height: 48px;
}

@media (max-aspect-ratio: 4/3) {
  .control-button :deep(.svg-responsive) {
    width: 40px;
    height: 40px;
  }
}
</style>