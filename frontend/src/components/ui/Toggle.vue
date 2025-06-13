<!-- frontend/src/components/ui/Toggle.vue -->
<template>
  <div class="toggle-component">
    <h3 v-if="title">{{ title }}</h3>
    
    <div class="toggle-container">
      <label class="toggle">
        <input 
          type="checkbox" 
          :checked="modelValue"
          @change="handleToggle"
          :disabled="disabled"
        >
        <span class="slider"></span>
      </label>
      <span class="toggle-label">
        {{ modelValue ? onLabel : offLabel }}
      </span>
    </div>
    
    <div class="status-info" v-if="statusText">
      <span :class="['status-dot', modelValue ? 'enabled' : 'disabled']"></span>
      <span class="status-text">{{ statusText }}</span>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  modelValue: {
    type: Boolean,
    required: true
  },
  title: {
    type: String,
    default: ''
  },
  onLabel: {
    type: String,
    default: 'ON'
  },
  offLabel: {
    type: String,
    default: 'OFF'
  },
  statusText: {
    type: String,
    default: ''
  },
  disabled: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['update:modelValue', 'change']);

function handleToggle(event) {
  const newValue = event.target.checked;
  emit('update:modelValue', newValue);
  emit('change', newValue);
}
</script>

<style scoped>
.toggle-component {
  background: white;
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 16px;
  margin: 16px 0;
}

.toggle-component h3 {
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
  min-width: 32px;
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

.status-dot.enabled {
  background-color: #2196F3;
}

.status-dot.disabled {
  background-color: #999;
}
</style>