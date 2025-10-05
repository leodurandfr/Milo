<!-- frontend/src/components/ui/ModalHeader.vue -->
<template>
  <div class="modal-header" :class="{ 'has-back': showBack }">
    <div v-if="showBack" class="back-modal-header">
      <IconButton icon="caretLeft" variant="dark" @click="handleBack" />
      <h2 class="heading-2">{{ title }}</h2>
    </div>
    <h2 v-else class="heading-2">{{ title }}</h2>
    <div v-if="$slots.actions" class="actions-wrapper">
      <slot name="actions"></slot>
    </div>
  </div>
</template>

<script setup>
import IconButton from './IconButton.vue';

const props = defineProps({
  title: {
    type: String,
    required: true
  },
  showBack: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['back']);

function handleBack() {
  emit('back');
}
</script>

<style scoped>
.modal-header {
  display: flex;
  background: var(--color-background-contrast);
  border-radius: var(--radius-04);
  padding: var(--space-04) var(--space-04) var(--space-04) var(--space-05);
    min-height: 72px;
  align-items: center;
  justify-content: space-between;
}

.modal-header.has-back {
  padding: var(--space-04);
}

.modal-header h2 {
  color: var(--color-text-contrast);
  flex: 1;
}

.back-modal-header {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  flex: 1;
}

.actions-wrapper {
  display: flex;
  align-items: center;
  gap: 12px;
}
@media (max-aspect-ratio: 4/3) {
    .modal-header {
        min-height: 64px;
    }
}
</style>