<template>
    <div class="controls">
      <button @click="onPrevious" class="control-button previous">
        <span>⏮️</span>
      </button>
      <button @click="onPlayPause" class="control-button play-pause">
        <span>{{ isPlaying ? '⏸️' : '▶️' }}</span>
      </button>
      <button @click="onNext" class="control-button next">
        <span>⏭️</span>
      </button>
    </div>
  </template>
  
  <script setup>
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
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 1.5rem;
  }
  
  .control-button {
    background: none;
    border: none;
    cursor: pointer;
    color: white;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 50px;
    height: 50px;
    border-radius: 50%;
    transition: background-color 0.2s;
    font-size: 1.5rem;
  }
  
  .control-button:hover {
    background-color: rgba(255, 255, 255, 0.1);
  }
  
  .control-button.play-pause {
    width: 60px;
    height: 60px;
    font-size: 2rem;
  }
  </style>