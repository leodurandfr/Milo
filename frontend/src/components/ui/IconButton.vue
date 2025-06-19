<!-- frontend/src/components/ui/IconButton.vue -->
<template>
  <button 
    class="icon-button"
    :disabled="disabled"
    @click="handleClick"
  >
    {{ icon }}
  </button>
</template>

<script setup>
const props = defineProps({
  icon: {
    type: String,
    required: true
  },
  disabled: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['click']);

function handleClick() {
  emit('click');
}
</script>

<style scoped>
.icon-button {
  width: 40px;
  height: 40px;
  border: 1px solid #ddd;
  background: white;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  transition: background-color 0.2s;
}

.icon-button:hover:not(:disabled) {
  background: #f5f5f5;
}

.icon-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>