<template>
    <div class="progress-bar" v-if="duration > 0">
      <span class="time current">{{ formatTime(currentPosition) }}</span>
      <div class="progress-container" @click="onProgressClick">
        <div class="progress" :style="{ width: progressPercentage + '%' }"></div>
      </div>
      <span class="time total">{{ formatTime(duration) }}</span>
    </div>
  </template>
  
  <script setup>
  import { computed } from 'vue';
  
  const props = defineProps({
    currentPosition: {
      type: Number,
      default: 0
    },
    duration: {
      type: Number,
      default: 0
    },
    progressPercentage: {
      type: Number,
      default: 0
    }
  });
  
  const emit = defineEmits(['seek']);
  
  function formatTime(ms) {
    if (!ms) return '0:00';
    const totalSeconds = Math.floor(ms / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${minutes}:${seconds.toString().padStart(2, '0')}`;
  }
  
  function onProgressClick(event) {
    if (!props.duration) return;
  
    const container = event.currentTarget;
    const rect = container.getBoundingClientRect();
    const offsetX = event.clientX - rect.left;
    const percentage = offsetX / rect.width;
  
    // Calculer la nouvelle position en ms
    const newPosition = Math.floor(props.duration * percentage);
    
    console.log(`Seek via clic: pourcentage=${percentage.toFixed(2)}, position=${newPosition}ms`);
    
    // Émettre l'événement vers le parent
    emit('seek', newPosition);
  }
  </script>
  
  <style scoped>
  .progress-bar {
    display: flex;
    align-items: center;
    width: 100%;
    margin-bottom: 1.5rem;
  }
  
  .progress-container {
    flex-grow: 1;
    height: 8px;
    background-color: #4D4D4D;
    border-radius: 4px;
    margin: 0 10px;
    cursor: pointer;
    position: relative;
  }
  
  .progress {
    height: 100%;
    background-color: #1DB954;
    border-radius: 4px;
    transition: width 0.1s linear;
  }
  
  .time {
    font-size: 0.8rem;
    opacity: 0.8;
    font-variant-numeric: tabular-nums;
    min-width: 40px;
    text-align: center;
  }
  </style>