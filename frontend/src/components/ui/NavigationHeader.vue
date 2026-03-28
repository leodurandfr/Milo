<!-- frontend/src/components/ui/NavigationHeader.vue -->
<template>
  <div class="navigation-header" :class="{
    'has-back': showBack,
    'variant-background-neutral': variant === 'background-neutral',
  }">
    <!-- Content container with fixed height -->
    <div class="header-content">
      <Transition name="header-fade" mode="out-in">
        <div v-if="showBack" :key="'back-' + title + '-' + subtitle" class="back-navigation-header">
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
.navigation-header {
  position: relative;
  display: flex;
  background: var(--color-background-contrast);
  border-radius: var(--radius-06);
  padding: var(--space-03);
  min-height: 72px;
  align-items: center;
  gap: var(--space-03);
}

.navigation-header.variant-background-neutral {
  background: var(--color-background-neutral);
}

.navigation-header.variant-background-neutral h2 {
  color: var(--color-text);
}

.navigation-header h2 {
  color: var(--color-text-contrast);
  margin: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Header content - grid stacking ensures both entering/leaving elements overlap cleanly */
.header-content {
  flex: 1;
  min-width: 0;
  display: grid;
  align-items: center;
  padding-right: 56px;
}

.header-content > * {
  grid-row: 1;
  grid-column: 1;
}

.back-navigation-header {
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
  padding-left: calc(var(--space-05) - var(--space-03));
}

.header-icon {
  flex-shrink: 0;
}

/* Actions container - absolute right so it never shifts during cross-fade */
.actions-container {
  position: absolute;
  right: var(--space-03);
  top: 0;
  bottom: 0;
  display: grid;
  align-items: center;
  justify-items: end;
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

/* Toggle needs extra right margin to sit at --space-04 from the header edge */
.actions-wrapper :slotted(.toggle-container) {
  margin-right: calc(var(--space-04) - var(--space-03));
}

.title-subtitle {
  color: var(--color-text-contrast-50);
  margin-right: var(--space-02);
}

.title-main {
  color: var(--color-text-contrast);
}

.navigation-header.variant-background-neutral .title-subtitle {
  color: var(--color-text-secondary);
}

.navigation-header.variant-background-neutral .title-main {
  color: var(--color-text);
}

/* Header content cross-fade transition - aligned with fade-slide body transition */
/* iOS WebKit requires transform to properly animate opacity */
.header-fade-leave-active {
  transition: opacity var(--transition-fast-leave);
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
  transition: opacity var(--transition-fast-leave);
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
  .navigation-header {
    min-height: 64px;
    padding: var(--space-03);
    border-radius: var(--radius-05);
  }

  .title-only {
    padding-left: calc(var(--space-06) - var(--space-03));
    padding-top: calc(var(--space-04) - var(--space-03));
    padding-bottom: calc(var(--space-04) - var(--space-03));
  }

  .header-icon {
    width: 40px !important;
    height: 40px !important;
    --icon-size: 40px;
  }
}
</style>
