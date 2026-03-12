<!-- frontend/src/components/setup/StepIndicator.vue -->
<template>
  <div class="step-indicator">
    <span
      v-for="step in total"
      :key="step"
      class="step-dot"
      :class="{ 'step-dot--active': step - 1 === current }"
    />
  </div>
</template>

<script setup>
defineProps({
  current: {
    type: Number,
    required: true,
  },
  total: {
    type: Number,
    required: true,
  },
});
</script>

<style scoped>
.step-indicator {
  display: flex;
  justify-content: center;
  gap: var(--space-02);
}

.step-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-background-strong);
  transition: background 0.2s ease;
}

.step-dot--active {
  background: var(--color-brand);
}
</style>
