<!-- Shared setting item - label + control wrapper -->
<template>
  <div class="setting-item">
    <div v-if="label" class="setting-item__label text-mono-medium">{{ label }}</div>
    <slot />
  </div>
</template>

<script setup>
defineProps({
  label: {
    type: String,
    default: ''
  }
});
</script>

<style scoped>
.setting-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.setting-item__label {
  color: var(--color-text-secondary);
}
</style>
