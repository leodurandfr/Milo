<!-- frontend/src/components/ui/Dropdown.vue -->
<template>
  <div ref="dropdownRef" class="dropdown">
    <button type="button" class="dropdown-trigger text-body-small" :class="{ 'is-open': isOpen }"
      @click="toggleDropdown">
      <span class="dropdown-label">{{ selectedLabel }}</span>
      <Icon name="caretDown" :size="24" class="dropdown-icon" />
    </button>

    <Transition name="dropdown-menu">
      <div v-if="isOpen" class="dropdown-menu">
        <div v-for="(option, index) in options" :key="option.value" class="dropdown-item text-body-small"
          :class="{ 'is-selected': option.value === modelValue }" @click="selectOption(option.value)">
          {{ option.label }}
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue';
import Icon from '@/components/ui/Icon.vue';

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
  }
});

const emit = defineEmits(['update:modelValue', 'change']);

const dropdownRef = ref(null);
const isOpen = ref(false);

const selectedLabel = computed(() => {
  const selected = props.options.find(opt => opt.value === props.modelValue);
  return selected ? selected.label : props.placeholder;
});

function toggleDropdown() {
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
  padding: var(--space-03) var(--space-05);
  border-radius: var(--radius-04);
  color: var(--color-text-secondary);
  background: var(--color-background-neutral);
  cursor: pointer;
  outline: none;
  gap: var(--space-01);
  transition: box-shadow var(--transition-fast);

  -webkit-box-shadow: inset 0px 0px 0px 2px var(--color-background-neutral);
  -moz-box-shadow: inset 0px 0px 0px 2px var(--color-background-neutral);
  box-shadow: inset 0px 0px 0px 2px var(--color-background-neutral);
}

.dropdown-trigger:focus {
  border-color: var(--color-brand);
  box-shadow: inset 0 0 0 2px var(--color-brand);

}

.dropdown-label {
  flex: 1;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

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

.dropdown-item {
  position: relative;
  padding: var(--space-03) var(--space-04);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition:
    background-color var(--transition-fast),
    color var(--transition-fast);
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

.dropdown-menu-enter-from {
  opacity: 0;
  transform: translateY(-8px);
}

.dropdown-menu-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}
</style>
