<template>
  <Transition name="notification-slide">
    <div v-if="title" class="notification-banner">
      <div class="notification-header">
        <div class="notification-title text-mono">{{ title }}</div>
        <button v-if="dismissable" @click="$emit('dismiss')" class="dismiss-btn">
          <Icon name="close" :size="16" />
        </button>
      </div>
      <div v-if="detail" class="notification-detail text-mono">{{ detail }}</div>
    </div>
  </Transition>
</template>

<script setup>
import Icon from './SvgIcon.vue';

defineProps({
  title: {
    type: String,
    default: null
  },
  detail: {
    type: String,
    default: null
  },
  dismissable: {
    type: Boolean,
    default: false
  }
});

defineEmits(['dismiss']);
</script>

<style scoped>
.notification-banner {
  position: fixed;
  top: var(--space-03);
  left: 50%;
  transform: translateX(-50%);
  max-width: 400px;
  z-index: 9999;
  padding: var(--space-03) var(--space-04);
  background: var(--color-background-contrast);
  border-radius: var(--radius-04);
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.notification-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-03);
}

.notification-title {
  color: var(--color-text-contrast);
  white-space: nowrap;
}

.notification-detail {
  color: var(--color-text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}

.dismiss-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--color-text-contrast-50);
  cursor: pointer;
  border-radius: var(--radius-01);
  transition: color var(--transition-fast), background var(--transition-fast);
  flex-shrink: 0;
}

.dismiss-btn:hover {
  color: var(--color-text-contrast);
}

/* Slide-in animation from top */
.notification-slide-enter-active {
  transition: transform var(--transition-spring), opacity var(--transition-spring);
}

.notification-slide-leave-active {
  transition: transform var(--transition-fast), opacity var(--transition-fast);
}

.notification-slide-enter-from {
  transform: translateX(-50%) translateY(-100%);
  opacity: 0;
}

.notification-slide-leave-to {
  transform: translateX(-50%) translateY(-100%);
  opacity: 0;
}


@media (max-aspect-ratio: 4/3) {
  .notification-banner {
    top: 16px;
    left: 16px;
    width: calc(100% - 32px);
    transform: none;
    max-width: none;
  }

  .notification-slide-enter-from,
  .notification-slide-leave-to {
    transform: translateY(-100%);
  }
}
</style>

<style>
.ios-app .notification-banner {
  top: 40px;
}
</style>
