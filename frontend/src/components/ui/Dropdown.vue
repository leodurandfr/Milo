<!-- frontend/src/components/ui/Dropdown.vue -->
<template>
  <div ref="dropdownRef" class="dropdown" :class="{ 'dropdown--custom-trigger': $slots.trigger }">
    <!-- A caller-drawn trigger (an IconButton) replaces the select box; the menu
         is the same. It must stay the wrapper's first element: the menu is
         positioned against it. -->
    <div v-if="$slots.trigger" class="dropdown-custom-trigger">
      <slot name="trigger" :toggle="toggleDropdown" :is-open="isOpen" :disabled="disabled" />
    </div>
    <button v-else v-press type="button" class="dropdown-trigger"
      :class="[`dropdown-trigger--${variant}`, `dropdown-trigger--${size}`, { 'is-open': isOpen, 'has-selection': modelValue }]"
      :disabled="disabled"
      @click="toggleDropdown">
      <span class="dropdown-label heading-4">{{ selectedLabel }}</span>
      <SvgIcon name="caretDown" :size="size === 'small' ? 20 : 24" class="dropdown-icon" />
    </button>

    <Teleport to="body">
      <Transition name="dropdown-menu">
        <div v-if="isOpen" ref="menuRef" class="dropdown-menu glass-shell"
          :class="[`dropdown-menu--${size}`, { 'icons-start': iconPlacement === 'start', 'open-upward': openUpward, 'open-leftward': openLeftward }]"
          :style="{ top: menuPosition.top, left: menuPosition.left, minWidth: menuPosition.width }">
          <!-- The list scrolls inside the glass, never the glass itself: its rim
               is an absolutely placed layer that would scroll away with it. -->
          <div class="dropdown-list" @scroll.stop>
            <div v-if="title" class="dropdown-title text-mono-small">{{ title }}</div>
            <div v-for="(option, index) in options" :key="option.value" class="dropdown-item"
              :class="['heading-4', { 'is-selected': option.value === modelValue }]"
              @click="selectOption(option.value)">
              <span class="dropdown-item-label">{{ option.label }}</span>
              <span v-if="option.icon" class="dropdown-item-icon" :role="option.iconLabel ? 'img' : null"
                :aria-label="option.iconLabel" :aria-hidden="option.iconLabel ? null : 'true'">
                <SvgIcon :name="option.icon" :size="20" />
              </span>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, watch, onBeforeUnmount } from 'vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';

const props = defineProps({
  modelValue: {
    type: String,
    default: ''
  },
  options: {
    type: Array,
    required: true,
    // Expected format: [{ label: 'Label', value: 'value' }, ...], plus an
    // optional `icon` beside the label (`iconPlacement`) and the `iconLabel` a
    // screen reader says — without one, the icon is decorative and hidden.
  },
  placeholder: {
    type: String,
    default: 'Select an option'
  },
  disabled: {
    type: Boolean,
    default: false
  },
  variant: {
    type: String,
    default: 'filled',
    validator: (value) => ['filled', 'plain'].includes(value)
  },
  size: {
    type: String,
    default: 'medium',
    validator: (value) => ['medium', 'small'].includes(value)
  },
  displayOverride: {
    type: String,
    default: null
  },
  // Where the menu goes when there is room for it: below the trigger from its
  // left edge, or above it from its right edge (a trigger at the end of a row).
  // Either flips when the preferred side lacks the room.
  // A heading above the options, for a menu whose trigger names nothing (an icon).
  title: {
    type: String,
    default: null
  },
  placement: {
    type: String,
    default: 'bottom-start',
    validator: (value) => ['bottom-start', 'top-end'].includes(value)
  },
  // Where an option's icon sits: after its label (a mark on a choice), or
  // before it (the icon of an action, as in a ⋯ menu).
  iconPlacement: {
    type: String,
    default: 'end',
    validator: (value) => ['end', 'start'].includes(value)
  }
});

const emit = defineEmits(['update:modelValue', 'change']);

const dropdownRef = ref(null);
const menuRef = ref(null);
const isOpen = ref(false);
const openUpward = ref(false);
const openLeftward = ref(false);
const menuPosition = ref({ top: '0px', left: '0px', width: '0px' });

const selectedLabel = computed(() => {
  if (props.displayOverride) return props.displayOverride;
  const selected = props.options.find(opt => opt.value === props.modelValue);
  return selected ? selected.label : props.placeholder;
});

/**
 * The trigger's viewport box *at rest*.
 *
 * The menu is positioned one tick after the click, while v-press still holds
 * its scale-down on the trigger — reading the raw BCR there is what made the
 * menu narrower than the button and nudged it right. So take the untransformed
 * layout size (`offsetWidth`, transform-immune) and rebuild the box around the
 * centre, which the press transform leaves in place.
 */
function getTriggerRestRect() {
  const wrapper = dropdownRef.value;
  const trigger = wrapper?.firstElementChild;
  if (!trigger) return null;

  const pressed = trigger.getBoundingClientRect();
  const width = trigger.offsetWidth;
  const height = trigger.offsetHeight;
  const centerX = pressed.left + pressed.width / 2;
  const centerY = pressed.top + pressed.height / 2;

  return {
    width,
    height,
    left: centerX - width / 2,
    right: centerX + width / 2,
    top: centerY - height / 2,
    bottom: centerY + height / 2
  };
}

function calculateDropdownDirection() {
  if (!dropdownRef.value) return;

  const BOTTOM_MARGIN = 24; // Minimum margin in pixels
  const MENU_MAX_HEIGHT = 340; // Max height of dropdown menu (CSS max-height)

  const triggerRect = getTriggerRestRect();
  if (!triggerRect) return;

  const GAP = 4; // 4px gap below the trigger

  // Get actual menu height if available (after render), otherwise use max
  const actualMenuHeight = menuRef.value?.offsetHeight || MENU_MAX_HEIGHT;

  // Horizontal: the preferred edge, unless the menu would leave the viewport.
  const MENU_MIN_WIDTH = 200; // CSS min-width of dropdown-menu
  const menuWidth = menuRef.value?.offsetWidth || Math.max(MENU_MIN_WIDTH, triggerRect.width);
  const fitsRightward = window.innerWidth - triggerRect.left >= menuWidth;
  const fitsLeftward = triggerRect.right >= menuWidth;
  openLeftward.value = props.placement === 'top-end'
    ? fitsLeftward || !fitsRightward
    : !fitsRightward && fitsLeftward;

  // Vertical: measured against the scrollable parent, or the viewport.
  let scrollableParent = dropdownRef.value.parentElement;
  while (scrollableParent) {
    const overflowY = window.getComputedStyle(scrollableParent).overflowY;
    if (overflowY === 'auto' || overflowY === 'scroll') break;
    scrollableParent = scrollableParent.parentElement;
  }
  const bounds = scrollableParent
    ? scrollableParent.getBoundingClientRect()
    : { top: 0, bottom: window.innerHeight };
  const spaceBelow = bounds.bottom - triggerRect.bottom;
  const spaceAbove = triggerRect.top - bounds.top;
  const needed = actualMenuHeight + BOTTOM_MARGIN;
  openUpward.value = props.placement === 'top-end'
    ? spaceAbove >= needed || spaceAbove > spaceBelow
    : spaceBelow < needed && spaceAbove > spaceBelow;

  menuPosition.value = {
    top: `${openUpward.value ? triggerRect.top - actualMenuHeight - GAP : triggerRect.bottom + GAP}px`,
    left: `${openLeftward.value ? triggerRect.right - menuWidth : triggerRect.left}px`,
    width: `${triggerRect.width}px`
  };
}

/**
 * Commit the start of the enter transition once the direction is known.
 *
 * The menu is inserted with the transition already armed, and the direction is
 * only decided a tick later — so the browser animates the *correction* rather
 * than applying it: an upward menu was measured travelling -8px -> 0, i.e.
 * downward, while wearing `open-upward` from its first frame. A reflow with
 * transitions off pins the corrected value, leaving only the from -> to leg to
 * animate. The reflow is harmless when the direction did not change.
 */
function settleEnterTransform() {
  const el = menuRef.value;
  if (!el) return;
  el.style.transition = 'none';
  void el.offsetHeight; // forces the corrected transform to be committed untransitioned
  el.style.transition = '';
}

async function toggleDropdown() {
  if (!isOpen.value) {
    // Reset direction defaults (recalculated after render with actual dimensions)
    openUpward.value = false;
    openLeftward.value = false;

    isOpen.value = true;

    // Calculate direction after menu renders to get actual height
    // Runs as microtask before CSS transition starts (double-rAF)
    await nextTick();
    calculateDropdownDirection();
    await nextTick();
    settleEnterTransform();
  } else {
    isOpen.value = false;
  }
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

function handleResize() {
  if (isOpen.value) {
    calculateDropdownDirection();
  }
}

// Any scroll outside the menu closes it: the trigger it hangs from has moved,
// and a menu chasing it across a scrolling list reads as detached.
function handleScroll(event) {
  if (!isOpen.value) return;
  if (menuRef.value?.contains(event.target)) return;
  isOpen.value = false;
}

// Listened to only while open: a tracklist mounts one menu per row, and a
// thousand rows each hearing every scroll event is felt on the Pi.
function listen(on) {
  const method = on ? 'addEventListener' : 'removeEventListener';
  document[method]('click', handleClickOutside);
  window[method]('resize', handleResize);
  window[method]('scroll', handleScroll, true); // Use capture phase for all scroll events
}

watch(isOpen, listen);

onBeforeUnmount(() => {
  if (isOpen.value) listen(false);
});
</script>

<style scoped>
.dropdown {
  position: relative;
  display: flex;
  width: 100%;
  flex: 1;
}

.dropdown--custom-trigger {
  width: auto;
  flex: none;
}

.dropdown-custom-trigger {
  display: flex;
}

.dropdown-trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  height: 48px;
  padding: 0 var(--space-03) 0 var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-control);
  cursor: pointer;
  outline: none;
  gap: var(--space-01);
  transition: background-color var(--transition-fast), box-shadow var(--transition-fast), opacity var(--transition-fast), var(--transition-press);
}

.dropdown-trigger--small {
  height: 36px;
  padding: 0 var(--space-02) 0 var(--space-03);
  border-radius: var(--radius-03);
}

/* Open: the trigger lifts out of its track, as the ButtonGroup's thumb does. */
.dropdown-trigger.is-open {
  background: var(--color-panel);
  box-shadow: 0 0 0 1px var(--color-border), var(--shadow-thumb);
}

.dropdown-trigger--plain {
  background: transparent;
}

.dropdown-trigger:disabled {
  opacity: var(--opacity-disabled);
  cursor: not-allowed;
}

.dropdown-label {
  flex: 1;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 42px;
  color: var(--color-text-secondary);
  transition: color var(--transition-fast);
}

.dropdown-trigger.has-selection .dropdown-label {
  color: var(--color-text);
}

.dropdown-icon {
  flex-shrink: 0;
  color: var(--color-text-secondary);
  transition: transform var(--transition-fast), color var(--transition-fast);
}

.dropdown-trigger.is-open .dropdown-icon {
  transform: rotate(180deg);
}

/* The menu floats, so it is floating chrome's material: the glass. The
   picked option is a brand tint across the row, the others plain. */
.dropdown-menu {
  position: fixed;
  z-index: 5001;
  border-radius: var(--radius-05);
  overflow: hidden;
  min-width: 200px;
  transform-origin: top center;
}

.dropdown-menu.open-upward {
  transform-origin: bottom center;
}

.dropdown-list {
  max-height: 340px;
  overflow-y: auto;
  padding: 6px;
}

.dropdown-menu--small .dropdown-item {
  padding: var(--space-02) var(--space-03);
}

.dropdown-item-icon {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.dropdown-menu.icons-start .dropdown-item {
  gap: var(--space-03);
}

.dropdown-menu.icons-start .dropdown-item-icon {
  order: -1;
}

.dropdown-menu.icons-start .dropdown-item:not(.is-selected) .dropdown-item-icon {
  color: var(--color-text-secondary);
}

.dropdown-title {
  padding: var(--space-02) var(--space-03) var(--space-01);
  color: var(--color-text-secondary);
}

.dropdown-item {
  position: relative;
  padding: 10px var(--space-03);
  border-radius: var(--radius-04);
  color: var(--color-text);
  cursor: pointer;
  transition:
    background-color var(--transition-fast),
    color var(--transition-fast);
  display: flex;
  align-items: center;
  gap: var(--space-01);
}

.dropdown-item-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dropdown-item:not(.is-selected):active {
  background: var(--color-shell-control);
}

.dropdown-item.is-selected {
  background: var(--color-brand-subtle);
  color: var(--color-brand);
}

/* Open: fades in and settles from the trigger on the spring; close: fades
   back without one. */
.dropdown-menu-enter-active {
  transition:
    opacity var(--transition-fast),
    transform var(--transition-spring-light);
}

.dropdown-menu-leave-active {
  transition:
    opacity 150ms var(--easeInCubic),
    transform 150ms var(--easeInCubic);
}

.dropdown-menu-enter-from,
.dropdown-menu-leave-to {
  opacity: 0;
  transform: translateY(-6px) scale(0.98);
}

.dropdown-menu.open-upward.dropdown-menu-enter-from,
.dropdown-menu.open-upward.dropdown-menu-leave-to {
  transform: translateY(6px) scale(0.98);
}

@media (max-aspect-ratio: 4/3) {
  .dropdown-trigger--small {
    height: 34px;
  }
}
</style>
