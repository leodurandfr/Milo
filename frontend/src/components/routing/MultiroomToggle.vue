<template>
  <div class="multiroom-toggle">
    <h3>Audio Output</h3>
    <div class="toggle-container">
      <label class="toggle">
        <input 
          type="checkbox" 
          :checked="isMultiroom"
          @change="handleToggle"
          :disabled="unifiedStore.isTransitioning"
        >
        <span class="slider"></span>
      </label>
      <span class="toggle-label">
        {{ isMultiroom ? 'Multiroom' : 'Direct' }}
      </span>
    </div>
    <div class="status-info">
      <span :class="['status-dot', isMultiroom ? 'multiroom' : 'direct']"></span>
      <span class="status-text">
        {{ isMultiroom ? 'Audio via Snapserver' : 'Audio direct HiFiBerry' }}
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

const unifiedStore = useUnifiedAudioStore();

const isMultiroom = computed(() => 
  unifiedStore.routingMode === 'multiroom'
);

async function handleToggle(event) {
  const newMode = event.target.checked ? 'multiroom' : 'direct';
  await unifiedStore.setRoutingMode(newMode);
}

// OPTIM: Faire confiance uniquement au WebSocket, pas de fallback API
console.log('MultiroomToggle mounted, waiting for WebSocket state...');
</script>

<style scoped>
.multiroom-toggle {
  background: white;
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 16px;
  margin: 16px 0;
}

.multiroom-toggle h3 {
  margin: 0 0 12px 0;
  color: #333;
  font-size: 16px;
}

.toggle-container {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.toggle {
  position: relative;
  display: inline-block;
  width: 50px;
  height: 24px;
}

.toggle input {
  opacity: 0;
  width: 0;
  height: 0;
}

.slider {
  position: absolute;
  cursor: pointer;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: #ccc;
  transition: 0.3s;
  border-radius: 24px;
}

.slider:before {
  position: absolute;
  content: "";
  height: 18px;
  width: 18px;
  left: 3px;
  bottom: 3px;
  background-color: white;
  transition: 0.3s;
  border-radius: 50%;
}

input:checked + .slider {
  background-color: #2196F3;
}

input:checked + .slider:before {
  transform: translateX(26px);
}

input:disabled + .slider {
  background-color: #999;
  cursor: not-allowed;
}

.toggle-label {
  font-weight: bold;
  color: #333;
}

.status-info {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #666;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.status-dot.multiroom {
  background-color: #2196F3;
}

.status-dot.direct {
  background-color: #4CAF50;
}
</style>