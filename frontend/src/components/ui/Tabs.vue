<!-- frontend/src/components/ui/Tabs.vue -->
<!-- Reusable tabs component with Button.vue-like styling -->
<template>
  <div class="tabs" :class="[`tabs--${size}`]">
    <button
      v-for="tab in tabs"
      :key="tab.value"
      v-press.light
      type="button"
      class="tab"
      :class="[
        sizeClass,
        modelValue === tab.value ? 'tab--active' : 'tab--inactive',
        { 'tab--disabled': tab.disabled }
      ]"
      :disabled="tab.disabled"
      @click="selectTab(tab.value)"
    >
      <SvgIcon v-if="tab.icon" :name="tab.icon" :size="iconSize" class="tab-icon" />
      <span class="tab-label">{{ tab.label }}</span>
      <span v-if="tab.badge" class="tab-badge">
        <SvgIcon :name="tab.badge" :size="12" />
      </span>
    </button>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import SvgIcon from './SvgIcon.vue';

const props = defineProps({
  modelValue: {
    type: String,
    default: ''
  },
  tabs: {
    type: Array,
    required: true
    // Expected format: [{ label: 'Label', value: 'value', icon?: 'iconName', badge?: 'badgeIcon', disabled?: boolean }]
  },
  size: {
    type: String,
    default: 'medium',
    validator: (value) => ['medium', 'small'].includes(value)
  }
});

const emit = defineEmits(['update:modelValue', 'change']);

// Typography class based on size (same as Button.vue)
const sizeClass = computed(() => props.size === 'small' ? 'heading-4' : 'heading-3');

const iconSize = computed(() => props.size === 'small' ? 20 : 24);

function selectTab(value) {
  if (value !== props.modelValue) {
    emit('update:modelValue', value);
    emit('change', value);
  }
}
</script>

<style scoped>
.tabs {
  display: flex;
  gap: var(--space-02);
  padding: var(--space-02);
  background: var(--color-background-neutral);
  border-radius: var(--radius-05);
  overflow-x: auto;
}

/* Hide scrollbar but allow scrolling */
.tabs::-webkit-scrollbar {
  height: 4px;
}

.tabs::-webkit-scrollbar-thumb {
  background: var(--color-border);
  border-radius: 2px;
}

/* Base tab style (matches Button.vue structure) */
.tab {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-02);
  text-align: center;
  border: none;
  cursor: pointer;
  transition: background-color var(--transition-fast), color var(--transition-fast), var(--transition-press);
  white-space: nowrap;
}

/* === SIZE: medium (matches Button.vue --medium) === */
.tabs--medium .tab {
  padding: 12px 16px;
  border-radius: var(--radius-04);
}

/* === SIZE: small (matches Button.vue --small) === */
.tabs--small .tab {
  height: 36px;
  padding: 8px 12px;
  border-radius: var(--radius-03);
}

/* === INACTIVE state (matches Button.vue --background-strong) === */
.tab--inactive {
  background-color: var(--color-background-strong);
  color: var(--color-text-secondary);
}

/* === ACTIVE state (matches Button.vue --brand) === */
.tab--active {
  background-color: var(--color-brand);
  color: var(--color-text-contrast);
}

/* === DISABLED state === */
.tab--disabled {
  background-color: var(--color-background);
  color: var(--color-text-light);
  cursor: not-allowed;
}

/* Icon styling */
.tab-icon {
  flex-shrink: 0;
}

.tab-label {
  overflow: hidden;
  text-overflow: ellipsis;
}

.tab-badge {
  display: flex;
  align-items: center;
  opacity: 0.8;
}

/* === RESPONSIVE (Mobile - matches Button.vue) === */
@media (max-aspect-ratio: 4/3) {
  .tabs {
    gap: var(--space-01);
    padding: var(--space-01);
  }

  .tabs--medium .tab {
    height: 38px;
    padding: 8px 16px;
    border-radius: var(--radius-03);
  }

  .tabs--small .tab {
    height: 34px;
    padding: 6px 12px;
  }
}
</style>
