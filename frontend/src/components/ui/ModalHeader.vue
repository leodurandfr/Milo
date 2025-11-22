<!-- frontend/src/components/ui/ModalHeader.vue -->
<template>
  <div class="modal-header" :class="{
    'has-back': showBack,
    'variant-background-neutral': variant === 'background-neutral',
    'has-icon': icon
  }">
    <div v-if="showBack" class="back-modal-header">
      <IconButton icon="caretLeft" :variant="variant === 'contrast' ? 'light' : 'default'" @click="handleBack" />
      <h2 v-if="!subtitle" class="heading-1">{{ title }}</h2>
      <h2 v-else class="heading-1">
        <span class="title-subtitle">{{ subtitle }}</span>
        <span class="title-main">{{ title }}</span>
      </h2>
    </div>
    <template v-else>
      <div v-if="icon" class="title-with-icon">
        <AppIcon :name="icon" :size="48" class="header-icon" />
        <h2 class="heading-1">{{ title }}</h2>
      </div>
      <h2 v-else class="heading-1">{{ title }}</h2>
    </template>
    <div v-if="$slots.actions" class="actions-wrapper">
      <slot name="actions" :iconVariant="variant === 'contrast' ? 'light' : 'default'"></slot>
    </div>
  </div>
</template>

<script setup>
import IconButton from './IconButton.vue';
import AppIcon from './AppIcon.vue';

const props = defineProps({
  title: {
    type: String,
    required: true
  },
  subtitle: {
    type: String,
    default: null
  },
  showBack: {
    type: Boolean,
    default: false
  },
  variant: {
    type: String,
    default: 'contrast', // 'contrast' ou 'background-neutral'
    validator: (value) => ['contrast', 'background-neutral'].includes(value)
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
  border-radius: var(--radius-06);
  padding: var(--space-04) var(--space-04) var(--space-04) var(--space-05);
  min-height: 72px;
  align-items: center;
  justify-content: space-between;
}

.modal-header.variant-background-neutral {
  background: var(--color-background-neutral);
}

.modal-header.variant-background-neutral h2 {
  color: var(--color-text);
}


.modal-header.has-icon,
.modal-header.has-back {
  padding: var(--space-03);
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
  gap: var(--space-02);
}

.title-subtitle {
  color: var(--color-text-secondary);
  margin-right: var(--space-02);
}

.title-main {
  color: var(--color-text);
}

@media (max-aspect-ratio: 4/3) {
  .modal-header {
    min-height: 64px;
    padding: var(--space-04) var(--space-04) var(--space-04) var(--space-06);
    border-radius: var(--radius-05);
  }


  .header-icon {
    width: 40px !important;
    height: 40px !important;
    --icon-size: 40px;
  }
}
</style>