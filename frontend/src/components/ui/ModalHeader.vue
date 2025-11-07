<!-- frontend/src/components/ui/ModalHeader.vue -->
<template>
  <div class="modal-header" :class="{
    'has-back': showBack,
    'variant-neutral': variant === 'neutral',
    'has-icon': icon
  }">
    <div v-if="showBack" class="back-modal-header">
      <CircularIcon icon="caretLeft" :variant="variant === 'neutral' ? 'light' : 'dark'" @click="handleBack" />
      <h2 class="heading-1">{{ title }}</h2>
    </div>
    <template v-else>
      <div v-if="icon" class="title-with-icon">
        <AppIcon :name="icon" :size="48" class="header-icon" />
        <h2 class="heading-1">{{ title }}</h2>
      </div>
      <h2 v-else class="heading-1">{{ title }}</h2>
    </template>
    <div v-if="$slots.actions" class="actions-wrapper">
      <slot name="actions"></slot>
    </div>
  </div>
</template>

<script setup>
import CircularIcon from './CircularIcon.vue';
import AppIcon from './AppIcon.vue';

const props = defineProps({
  title: {
    type: String,
    required: true
  },
  showBack: {
    type: Boolean,
    default: false
  },
  variant: {
    type: String,
    default: 'contrast', // 'contrast' ou 'neutral'
    validator: (value) => ['contrast', 'neutral'].includes(value)
  },
  icon: {
    type: String,
    default: null
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

.modal-header.variant-neutral {
  background: var(--color-background-neutral);
}

.modal-header.variant-neutral h2 {
  color: var(--color-text);
}

.modal-header.has-back {
  padding: var(--space-04);
}

.modal-header.has-icon {
  padding: var(--space-03) var(--space-04) var(--space-03) var(--space-03);
}

.modal-header h2 {
  color: var(--color-text-contrast);
  flex: 1;
}

.back-modal-header {
  display: flex;
  align-items: center;
  gap: var(--space-04);
  flex: 1;
}

.title-with-icon {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  flex: 1;
}

.header-icon {
  flex-shrink: 0;
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

  .modal-header.has-icon {
    padding: var(--space-03);
  }

  .header-icon {
    width: 40px !important;
    height: 40px !important;
    --icon-size: 40px;
  }
}
</style>