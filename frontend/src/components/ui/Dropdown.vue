<!-- frontend/src/components/ui/Dropdown.vue -->
<template>
  <div ref="dropdownRef" class="dropdown">
    <button type="button" class="dropdown-trigger"
      :class="[textClass, `dropdown-trigger--${variant}`, { 'is-open': isOpen, 'has-selection': modelValue }]"
      :disabled="disabled"
      @click="toggleDropdown">
      <span class="dropdown-label" :class="variant === 'transparent' ? 'text-mono' : 'text-body'">{{ selectedLabel }}</span>
      <SvgIcon v-if="variant === 'default'" name="caretDown" :size="24" class="dropdown-icon" />
    </button>

    <Transition name="dropdown-menu">
      <div v-if="isOpen" class="dropdown-menu" :class="{ 'open-upward': openUpward }">
        <div v-for="(option, index) in options" :key="option.value" class="dropdown-item"
          :class="[textClass, { 'is-selected': option.value === modelValue }]" @click="selectOption(option.value)">
          {{ option.label }}
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';

const props = defineProps({
  modelValue: {
    type: String,
    default: ''
  },
  options: {
    type: Array,
    required: true,
    // Expected format: [{ label: 'Label', value: 'value' }, ...]
  },
  placeholder: {
    type: String,
    default: 'Select an option'
  },
  disabled: {
    type: Boolean,
    default: false
  },
  size: {
    type: String,
    default: 'default',
    validator: (value) => ['default', 'small'].includes(value)
  },
  variant: {
    type: String,
    default: 'default',
    validator: (value) => ['default', 'minimal', 'transparent'].includes(value)
  }
});

const emit = defineEmits(['update:modelValue', 'change']);

const dropdownRef = ref(null);
const isOpen = ref(false);
const openUpward = ref(false);

const textClass = computed(() => {
  return props.size === 'small' ? 'text-body-small' : 'text-body';
});

const selectedLabel = computed(() => {
  const selected = props.options.find(opt => opt.value === props.modelValue);
  return selected ? selected.label : props.placeholder;
});

function calculateDropdownDirection() {
  if (!dropdownRef.value) return;

  const BOTTOM_MARGIN = 24; // Minimum margin in pixels
  const MENU_MAX_HEIGHT = 340; // Max height of dropdown menu

  // Get dropdown position
  const triggerRect = dropdownRef.value.getBoundingClientRect();

  // Find the scrollable parent container
  let scrollableParent = dropdownRef.value.parentElement;
  while (scrollableParent) {
    const style = window.getComputedStyle(scrollableParent);
    const overflowY = style.overflowY;

    if (overflowY === 'auto' || overflowY === 'scroll') {
      break;
    }
    scrollableParent = scrollableParent.parentElement;
  }

  // If no scrollable parent found, use viewport
  if (!scrollableParent) {
    const spaceBelow = window.innerHeight - triggerRect.bottom;
    const spaceAbove = triggerRect.top;
    openUpward.value = spaceBelow < (MENU_MAX_HEIGHT + BOTTOM_MARGIN) && spaceAbove > spaceBelow;
    return;
  }

  // Calculate space relative to scrollable parent
  const parentRect = scrollableParent.getBoundingClientRect();
  const spaceBelow = parentRect.bottom - triggerRect.bottom;
  const spaceAbove = triggerRect.top - parentRect.top;

  // Open upward if not enough space below and more space above than below
  openUpward.value = spaceBelow < (MENU_MAX_HEIGHT + BOTTOM_MARGIN) && spaceAbove > spaceBelow;
}

function toggleDropdown() {
  // Calculate direction BEFORE opening to avoid animation glitch
  if (!isOpen.value) {
    calculateDropdownDirection();
  }

  isOpen.value = !isOpen.value;
}

function selectOption(value) {
  emit('update:modelValue', value);
  emit('change', value);
  isOpen.value = false;
}

function handleClickOutside(event) {
  if (dropdownRef.value && !dropdownRef.value.contains(event.target)) {
    isOpen.value = false;
  }
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside);
});

onBeforeUnmount(() => {
  document.removeEventListener('click', handleClickOutside);
});
</script>

<style scoped>
.dropdown {
  position: relative;
  display: flex;
  width: 100%;
  flex: 1;
}

.dropdown-trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: var(--space-03) var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-background-neutral);
  cursor: pointer;
  outline: none;
  gap: var(--space-01);
  transition: box-shadow var(--transition-fast);

  -webkit-box-shadow: inset 0px 0px 0px 2px var(--color-border);
  -moz-box-shadow: inset 0px 0px 0px 2px var(--color-border);
  box-shadow: inset 0px 0px 0px 2px var(--color-border);
}

.dropdown-trigger:focus {
  border-color: var(--color-brand);
  box-shadow: inset 0 0 0 2px var(--color-brand);
}

.dropdown-trigger:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Minimal variant */
.dropdown-trigger--minimal {
  background: none;
  border: none;
  box-shadow: none;
  min-width: auto;
}

.dropdown-trigger--minimal:focus {
  box-shadow: none;
}

.dropdown-trigger--minimal .dropdown-label {
  color: var(--color-text-contrast-50);
  font-weight: normal;
}

/* Transparent variant */
.dropdown-trigger--transparent {
  background: none;
  border: none;
  box-shadow: none;
  width: auto;
  min-width: 48px;
  padding: var(--space-02) 0;
}

.dropdown-trigger--transparent:focus {
  box-shadow: none;
}

.dropdown-trigger--transparent .dropdown-label {
  color: var(--color-text-contrast-50);
  font-weight: normal;
  text-align: center;
  min-width: 48px;
}



.dropdown-label {
  flex: 1;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

/* .dropdown-trigger.has-selection .dropdown-label {
  color: var(--color-text);
} */

.dropdown-icon {
  flex-shrink: 0;
  color: var(--color-text-secondary);
  transition: transform var(--transition-fast);
}

.dropdown-trigger.is-open .dropdown-icon {
  transform: rotate(180deg);

}

.dropdown-menu {
  position: absolute;
  top: calc(100% + var(--space-01));
  left: 0;
  right: 0;
  z-index: 100;
  background: var(--color-background-neutral);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-04);
  box-shadow: var(--shadow-02);
  max-height: 340px;
  overflow-y: auto;
}

.dropdown-menu.open-upward {
  top: auto;
  bottom: calc(100% + var(--space-01));
}

.dropdown-item {
  position: relative;
  padding: var(--space-03) var(--space-04);
  color: var(--color-text);
  cursor: pointer;
  transition:
    background-color var(--transition-fast),
    color var(--transition-fast);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dropdown-item::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: var(--space-04);
  right: var(--space-04);
  height: 1px;
  background: var(--color-border);
}

.dropdown-item:last-child::after {
  display: none;
}

.dropdown-item.is-selected {
  color: var(--color-brand);
}

/* Transition animations */
.dropdown-menu-enter-active,
.dropdown-menu-leave-active {
  transition:
    opacity var(--transition-fast),
    transform var(--transition-fast);
  transform-origin: top;
}

.dropdown-menu.open-upward.dropdown-menu-enter-active,
.dropdown-menu.open-upward.dropdown-menu-leave-active {
  transform-origin: bottom;
}

.dropdown-menu-enter-from {
  opacity: 0;
  transform: translateY(-8px);
}

.dropdown-menu.open-upward.dropdown-menu-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

.dropdown-menu-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}

.dropdown-menu.open-upward.dropdown-menu-leave-to {
  opacity: 0;
  transform: translateY(8px);
}
</style>
