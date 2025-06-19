<!-- frontend/src/components/ui/Toggle.vue -->
<template>
  <div class="toggle-container">
    <h3 v-if="title">{{ title }}</h3>
    
    <label class="toggle">
      <input 
        type="checkbox" 
        :checked="modelValue"
        @change="handleToggle"
        :disabled="disabled"
      >
      <span class="slider"></span>
    </label>
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
.toggle-container {
  display: flex;
  align-items: center;
  gap: 12px;
}

.toggle-container h3 {
  margin: 0;
  color: #333;
  font-size: 16px;
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
  background-color:rgb(252, 155, 0);
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
  background-color:rgb(252, 155, 0);

}

input:checked + .slider:before {
  transform: translateX(26px);
}

input:disabled + .slider {
  background-color: #999;
  cursor: not-allowed;
}
</style>