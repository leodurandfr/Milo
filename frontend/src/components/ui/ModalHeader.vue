<!-- frontend/src/components/ui/ModalHeader.vue -->
<template>
  <div class="modal-header" :class="{
    'has-back': showBack,
    'variant-background-neutral': variant === 'background-neutral',
    'has-icon': icon
  }">
    <!-- Content container with fixed height -->
    <div class="header-content">
      <Transition name="header-fade">
        <div v-if="showBack" :key="'back-' + title + '-' + subtitle" class="back-modal-header">
          <IconButton icon="caretLeft" :variant="variant === 'contrast' ? 'on-dark' : 'background-strong'" @click="handleBack" />
          <h2 v-if="!subtitle" class="heading-1">{{ title }}</h2>
          <h2 v-else class="heading-1">
            <span class="title-subtitle">{{ subtitle }}</span>
            <span class="title-main">{{ title }}</span>
          </h2>
        </div>
        <div v-else-if="icon" :key="'icon-' + title + '-' + subtitle" class="title-with-icon">
          <AppIcon :name="icon" :size="48" class="header-icon" />
          <h2 v-if="!subtitle" class="heading-1">{{ title }}</h2>
          <h2 v-else class="heading-1">
            <span class="title-subtitle">{{ subtitle }}</span>
            <span class="title-main">{{ title }}</span>
          </h2>
        </div>
        <div v-else :key="'title-' + title + '-' + subtitle" class="title-only">
          <h2 v-if="!subtitle" class="heading-1">{{ title }}</h2>
          <h2 v-else class="heading-1">
            <span class="title-subtitle">{{ subtitle }}</span>
            <span class="title-main">{{ title }}</span>
          </h2>
        </div>
      </Transition>
    </div>

    <!-- Actions container -->
    <div class="actions-container">
      <Transition name="actions-fade">
        <div v-if="$slots.actions" :key="actionsKey" class="actions-wrapper">
          <slot name="actions" :iconVariant="variant === 'contrast' ? 'on-dark' : 'background-strong'"></slot>
        </div>
        <div v-else key="no-actions" class="actions-placeholder"></div>
      </Transition>
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
  },
  actionsKey: {
    type: String,
    default: 'default'
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
  padding: var(--space-03) var(--space-04) var(--space-03) var(--space-05);
  min-height: 72px;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-03);
  transition: padding var(--transition-ultra-fast);
}

.modal-header.variant-background-neutral {
  background: var(--color-background-neutral);
}

.modal-header.variant-background-neutral h2 {
  color: var(--color-text);
}

.modal-header.has-icon,
.modal-header.has-back {
  padding: var(--space-03) var(--space-04) var(--space-03) var(--space-03);
}

.modal-header h2 {
  color: var(--color-text-contrast);
  margin: 0;
}

/* Header content - grid stacking ensures both entering/leaving elements overlap cleanly */
.header-content {
  flex: 1;
  min-width: 0;
  display: grid;
  align-items: center;
}

.header-content > * {
  grid-row: 1;
  grid-column: 1;
}

.back-modal-header {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  width: 100%;
}

.title-with-icon {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  width: 100%;
}

.title-only {
  width: 100%;
}

.header-icon {
  flex-shrink: 0;
}

/* Actions container - grid stacking for cross-fade overlay */
.actions-container {
  flex-shrink: 0;
  display: grid;
  align-items: center;
}

.actions-container > * {
  grid-row: 1;
  grid-column: 1;
}

.actions-wrapper {
  display: flex;
  align-items: center;
  gap: var(--space-02);
}

.title-subtitle {
  color: var(--color-text-contrast-50);
  margin-right: var(--space-02);
}

.title-main {
  color: var(--color-text-contrast);
}

.modal-header.variant-background-neutral .title-subtitle {
  color: var(--color-text-secondary);
}

.modal-header.variant-background-neutral .title-main {
  color: var(--color-text);
}

/* Header content cross-fade transition - aligned with fade-slide body transition */
/* iOS WebKit requires transform to properly animate opacity */
.header-fade-leave-active {
  transition: opacity var(--transition-ultra-fast);
  transform: translate3d(0, 0, 0);
  -webkit-backface-visibility: hidden;
}

.header-fade-enter-active {
  transition: opacity var(--transition-in-out);
  transform: translate3d(0, 0, 0);
  -webkit-backface-visibility: hidden;
}

.header-fade-enter-from,
.header-fade-leave-to {
  opacity: 0;
}

/* Actions cross-fade transition - aligned with fade-slide body transition */
/* iOS WebKit requires transform to properly animate opacity */
.actions-fade-leave-active {
  transition: opacity var(--transition-ultra-fast);
  transform: translate3d(0, 0, 0);
  -webkit-backface-visibility: hidden;
}

.actions-fade-enter-active {
  transition: opacity var(--transition-in-out);
  transform: translate3d(0, 0, 0);
  -webkit-backface-visibility: hidden;
}

.actions-fade-enter-from,
.actions-fade-leave-to {
  opacity: 0;
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
