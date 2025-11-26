<!-- frontend/src/components/ui/ModalHeader.vue -->
<template>
  <div class="modal-header" :class="{
    'has-back': displayedShowBack,
    'variant-background-neutral': variant === 'background-neutral',
    'has-icon': displayedIcon
  }">
    <!-- Content container with fixed height -->
    <div class="header-content">
      <Transition name="header-fade" mode="out-in" @after-leave="onAfterLeave">
        <div v-if="showBack" :key="'back-' + title" class="back-modal-header">
          <IconButton icon="caretLeft" :variant="variant === 'contrast' ? 'light' : 'default'" @click="handleBack" />
          <h2 v-if="!subtitle" class="heading-1">{{ title }}</h2>
          <h2 v-else class="heading-1">
            <span class="title-subtitle">{{ subtitle }}</span>
            <span class="title-main">{{ title }}</span>
          </h2>
        </div>
        <div v-else-if="icon" :key="'icon-' + title" class="title-with-icon">
          <AppIcon :name="icon" :size="48" class="header-icon" />
          <h2 class="heading-1">{{ title }}</h2>
        </div>
        <div v-else :key="'title-' + title" class="title-only">
          <h2 class="heading-1">{{ title }}</h2>
        </div>
      </Transition>
    </div>

    <!-- Actions container -->
    <div class="actions-container">
      <Transition name="actions-fade" mode="out-in">
        <div v-if="$slots.actions" :key="actionsKey" class="actions-wrapper">
          <slot name="actions" :iconVariant="variant === 'contrast' ? 'light' : 'default'"></slot>
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue';
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

// Delayed state for CSS classes - only updates after fade-out completes
const displayedShowBack = ref(props.showBack);
const displayedIcon = ref(props.icon);

// Initialize on mount
onMounted(() => {
  displayedShowBack.value = props.showBack;
  displayedIcon.value = props.icon;
});

// Update displayed state after leave transition completes
function onAfterLeave() {
  displayedShowBack.value = props.showBack;
  displayedIcon.value = props.icon;
}

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
  gap: var(--space-03);
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
  margin: 0;
}

/* Header content - takes remaining space */
.header-content {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
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

/* Actions container - fixed width when empty to prevent layout shift */
.actions-container {
  flex-shrink: 0;
  display: flex;
  align-items: center;
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

/* Header content fade transition */
.header-fade-enter-active,
.header-fade-leave-active {
  transition: opacity var(--transition-fast);
}

.header-fade-enter-from,
.header-fade-leave-to {
  opacity: 0;
}

/* Actions fade transition */
.actions-fade-enter-active,
.actions-fade-leave-active {
  transition: opacity var(--transition-fast);
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

  .modal-header.has-icon,
  .modal-header.has-back {
    padding: var(--space-03);
  }

  .header-icon {
    width: 40px !important;
    height: 40px !important;
    --icon-size: 40px;
  }
}
</style>
