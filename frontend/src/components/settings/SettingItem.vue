<!-- Shared setting item - label + control wrapper. `inline` puts the label on
     the control's left (stacked again on a phone); `hint` sits under the control. -->
<template>
  <div class="setting-item" :class="{ 'setting-item--inline': inline }">
    <div v-if="label" class="setting-item__label text-body">{{ label }}</div>
    <slot />
    <span v-if="hint" class="setting-item__hint text-body-small">{{ hint }}</span>
  </div>
</template>

<script setup>
defineProps({
  label: {
    type: String,
    default: ''
  },
  hint: {
    type: String,
    default: ''
  },
  inline: {
    type: Boolean,
    default: false
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

.setting-item__hint {
  color: var(--color-text-tertiary);
}

.setting-item--inline {
  flex-direction: row;
  align-items: baseline;
}

.setting-item--inline > .setting-item__label {
  width: 33%;
  flex-shrink: 0;
}

.setting-item--inline > :not(.setting-item__label) {
  flex: 1;
  min-width: 0;
}

@media (max-aspect-ratio: 4/3) {
  .setting-item--inline {
    flex-direction: column;
    align-items: stretch;
  }

  .setting-item--inline > .setting-item__label {
    width: auto;
  }
}
</style>
