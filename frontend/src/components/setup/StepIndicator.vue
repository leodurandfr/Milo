<!-- frontend/src/components/setup/StepIndicator.vue -->
<template>
  <div class="step-indicator">
    <span
      v-for="step in total"
      :key="step"
      class="step-bar"
      :class="{
        'step-bar--completed': step - 1 < current,
        'step-bar--active': step - 1 === current,
      }"
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
  gap: var(--space-01);
  padding-top: var(--space-02);
}

.step-bar {
  flex: 1;
  height: 4px;
  border-radius: 2px;
  background: var(--color-background-strong);
  transition: background 0.3s ease;
}

.step-bar--completed,
.step-bar--active {
  background: var(--color-text-light);
}

</style>
