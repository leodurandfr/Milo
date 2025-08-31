<!-- frontend/src/components/ui/Toggle.vue - Font corrigée -->
<template>
  <div class="toggle-container">
    <h2 v-if="title" class="heading-2">{{ title }}</h2>
    
    <label :class="['toggle', `toggle--${variant}`]">
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
  },
  variant: {
    type: String,
    default: 'primary',
    validator: (value) => ['primary', 'secondary'].includes(value)
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

.toggle-container h2 {
  margin: 0;
  color: var(--color-text);
}

.toggle {
  position: relative;
  display: inline-block;
  width: 70px;
  height: 40px;
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
  border-radius: var(--radius-full);
  transition: background-color 0.2s ease;
}

.slider:before {
  position: absolute;
  content: "";
  height: 36px;
  width: 36px;
  left: 2px;
  bottom: 2px;
  background-color: var(--color-background-neutral);
  border-radius: var(--radius-full);
  transition: transform 0.2s ease;
}

/* Variantes de couleurs */
.toggle--primary .slider {
  background-color: var(--color-text-light);
}

.toggle--primary input:checked + .slider {
  background-color: var(--color-brand);
}

.toggle--secondary .slider {
  background-color: var(--color-background-strong);
}

.toggle--secondary input:checked + .slider {
  background-color: var(--color-background-contrast);
}

/* État activé */
input:checked + .slider:before {
  transform: translateX(30px);
}

/* État désactivé */
input:disabled + .slider {
  background-color: #999;
  cursor: not-allowed;
}

/* Responsive - Mobile (aspect ratio < 4/3) */
@media (max-aspect-ratio: 4/3) {
  .toggle {
    width: 56px;
    height: 32px;
  }
  
  .slider:before {
    height: 28px;
    width: 28px;
  }
  
  input:checked + .slider:before {
    transform: translateX(24px);
  }
}
</style>