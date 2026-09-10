<!-- frontend/src/components/ui/Dropdown.vue -->
<template>
  <div ref="dropdownRef" class="dropdown">
    <button v-press type="button" class="dropdown-trigger"
      :class="[`dropdown-trigger--${variant}`, `dropdown-trigger--${size}`, { 'is-open': isOpen, 'has-selection': modelValue }]"
      :disabled="disabled"
      @click="toggleDropdown">
      <span class="dropdown-label" :class="variant === 'minimal' ? 'text-mono-small' : (size === 'small' ? 'heading-4' : 'heading-3')">{{ selectedLabel }}</span>
      <SvgIcon v-if="variant !== 'minimal'" name="caretDown" :size="size === 'small' ? 20 : 24" class="dropdown-icon" />
    </button>

    <Teleport to="body">
      <Transition name="dropdown-menu">
        <div v-if="isOpen" ref="menuRef" class="dropdown-menu"
          :class="[`dropdown-menu--${size}`, { 'open-upward': openUpward, 'open-leftward': openLeftward }]"
          :style="{ top: menuPosition.top, left: menuPosition.left, minWidth: menuPosition.width }"
          @scroll.stop>
          <div v-for="(option, index) in options" :key="option.value" class="dropdown-item"
            :class="[size === 'small' ? 'heading-4' : 'heading-3', { 'is-selected': option.value === modelValue }]"
            @click="selectOption(option.value)">
            {{ option.label }}
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, onBeforeUnmount } from 'vue';
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
  variant: {
    type: String,
    default: 'outline',
    validator: (value) => ['outline', 'minimal', 'background-neutral'].includes(value)
  },
  size: {
    type: String,
    default: 'medium',
    validator: (value) => ['medium', 'small'].includes(value)
  },
  displayOverride: {
    type: String,
    default: null
  }
});

const emit = defineEmits(['update:modelValue', 'change']);

const dropdownRef = ref(null);
const menuRef = ref(null);
const isOpen = ref(false);
const openUpward = ref(false);
const openLeftward = ref(false);
const menuPosition = ref({ top: '0px', left: '0px', width: '0px' });
const lastScrollPosition = ref({ x: 0, y: 0 });

const selectedLabel = computed(() => {
  if (props.displayOverride) return props.displayOverride;
  const selected = props.options.find(opt => opt.value === props.modelValue);
  return selected ? selected.label : props.placeholder;
});

/**
 * The kiosk `ui_scale`, which the menu applies to itself.
 *
 * The menu teleports to `body`, outside the `#app` that transform sizes, so it
 * inherits nothing — `.dropdown-menu` re-applies the scale so its typography
 * matches the trigger's. Its own layout sizes are therefore app-space, and
 * every one of them has to be multiplied back to be compared with a viewport
 * distance. Read from the custom property rather than measured, so the number
 * here is by construction the one the CSS used.
 */
function getUiScale() {
  const scale = parseFloat(
    getComputedStyle(document.documentElement).getPropertyValue('--ui-scale')
  );
  return scale > 0 ? scale : 1;
}

/**
 * The trigger's viewport box *at rest*.
 *
 * The menu is positioned one tick after the click, while v-press still holds
 * its scale-down on the trigger — reading the raw BCR there is what made the
 * menu narrower than the button and nudged it right. So take the untransformed
 * layout size (`offsetWidth`, transform-immune), scale it by whatever the
 * ancestors apply (the kiosk `ui_scale` on #app — the menu teleports outside
 * it, so it must be positioned in scaled viewport coordinates), and rebuild the
 * box around the centre, which the press transform leaves in place.
 */
function getTriggerRestRect() {
  const wrapper = dropdownRef.value;
  const trigger = wrapper?.querySelector('.dropdown-trigger');
  if (!trigger) return null;

  const pressed = trigger.getBoundingClientRect();
  // The wrapper carries no transform of its own, so it reveals the ancestor scale.
  const scale = wrapper.offsetWidth > 0
    ? wrapper.getBoundingClientRect().width / wrapper.offsetWidth
    : 1;
  const width = trigger.offsetWidth * scale;
  const height = trigger.offsetHeight * scale;
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

  const menuScale = getUiScale();
  const GAP = 4 * menuScale; // 4px gap below the trigger, in the menu's own space
  // `min-width` is a layout size of the scaled menu, so it is app-space — unlike
  // top/left, which are viewport coordinates the transform leaves alone.
  const menuLayoutWidth = triggerRect.width / menuScale;

  // Get actual menu height if available (after render), otherwise use max
  const actualMenuHeight = (menuRef.value?.offsetHeight || MENU_MAX_HEIGHT) * menuScale;

  // Detect horizontal overflow: align right edge of menu to right edge of trigger
  const MENU_MIN_WIDTH = 200; // CSS min-width of dropdown-menu
  const menuWidth = (menuRef.value?.offsetWidth || Math.max(MENU_MIN_WIDTH, menuLayoutWidth)) * menuScale;
  const spaceRight = window.innerWidth - triggerRect.left;
  openLeftward.value = spaceRight < menuWidth && triggerRect.right > menuWidth;

  const left = openLeftward.value
    ? triggerRect.right - menuWidth
    : triggerRect.left;

  menuPosition.value = {
    top: `${triggerRect.bottom + GAP}px`,
    left: `${left}px`,
    width: `${menuLayoutWidth}px`
  };

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
    openUpward.value = spaceBelow < (actualMenuHeight + BOTTOM_MARGIN) && spaceAbove > spaceBelow;

    // Adjust position if opening upward - use actual menu height
    if (openUpward.value) {
      menuPosition.value.top = `${triggerRect.top - actualMenuHeight - GAP}px`;
    }
    return;
  }

  // Calculate space relative to scrollable parent
  const parentRect = scrollableParent.getBoundingClientRect();
  const spaceBelow = parentRect.bottom - triggerRect.bottom;
  const spaceAbove = triggerRect.top - parentRect.top;

  // Open upward if not enough space below and more space above than below
  openUpward.value = spaceBelow < (actualMenuHeight + BOTTOM_MARGIN) && spaceAbove > spaceBelow;

  // Adjust position if opening upward - use actual menu height
  if (openUpward.value) {
    menuPosition.value.top = `${triggerRect.top - actualMenuHeight - GAP}px`;
  }
}

async function toggleDropdown() {
  if (!isOpen.value) {
    // Reset direction defaults (recalculated after render with actual dimensions)
    openUpward.value = false;
    openLeftward.value = false;

    // Initialize scroll position for detection
    const target = dropdownRef.value?.parentElement;
    lastScrollPosition.value = {
      x: target?.scrollLeft || window.scrollX || 0,
      y: target?.scrollTop || window.scrollY || 0
    };

    isOpen.value = true;

    // Calculate direction after menu renders to get actual height
    // Runs as microtask before CSS transition starts (double-rAF)
    await nextTick();
    calculateDropdownDirection();
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

function handleScroll(event) {
  if (!isOpen.value) return;

  // Ignore scroll events from the dropdown menu itself (allows vertical scrolling inside)
  if (menuRef.value?.contains(event.target)) return;

  // Detect if scroll is horizontal or vertical
  const target = event.target === document ? window : event.target;
  const currentScrollX = target.scrollLeft || window.scrollX || 0;
  const currentScrollY = target.scrollTop || window.scrollY || 0;

  const deltaX = Math.abs(currentScrollX - lastScrollPosition.value.x);
  const deltaY = Math.abs(currentScrollY - lastScrollPosition.value.y);

  // Update position
  lastScrollPosition.value = { x: currentScrollX, y: currentScrollY };

  // If horizontal scroll detected, close dropdown
  if (deltaX > 0) {
    isOpen.value = false;
    return;
  }

  // If vertical scroll only, recalculate position
  if (deltaY > 0) {
    calculateDropdownDirection();
  }
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside);
  window.addEventListener('resize', handleResize);
  window.addEventListener('scroll', handleScroll, true); // Use capture phase for all scroll events
});

onBeforeUnmount(() => {
  document.removeEventListener('click', handleClickOutside);
  window.removeEventListener('resize', handleResize);
  window.removeEventListener('scroll', handleScroll, true);
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
  box-shadow: inset 0 0 0 2px var(--color-border);
  transition: box-shadow var(--transition-fast), var(--transition-press);
}

/* Size: small */
.dropdown-trigger--small {
  height: 36px;
  padding: var(--space-02) var(--space-03);
  border-radius: var(--radius-03);
}

.dropdown-trigger.is-open {
  -webkit-box-shadow: inset 0 0 0 2px var(--color-brand);
  box-shadow: inset 0 0 0 2px var(--color-brand);
}

.dropdown-trigger:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.dropdown-trigger:disabled .dropdown-label {
  color: var(--color-text-light);
}

/* Minimal variant */
.dropdown-trigger--minimal {
  background: none;
  border: none;
  box-shadow: none;
  width: auto;
  /* Vertical-only padding: no horizontal padding so the trigger hugs its content width */
  padding: var(--space-04) 0;
}

.dropdown-trigger--minimal:focus {
  box-shadow: none;
}

.dropdown-trigger--minimal .dropdown-label {
  color: var(--color-text-contrast-50);
  text-align: center;
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

.dropdown-trigger--outline.has-selection .dropdown-label {
  color: var(--color-text);
}

/* Background-neutral variant */
.dropdown-trigger--background-neutral {
  box-shadow: none;
}

.dropdown-trigger--background-neutral.has-selection .dropdown-label {
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

.dropdown-menu {
  position: fixed;
  z-index: 5001;
  background: var(--color-background-neutral);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-04);
  box-shadow: var(--shadow-02);
  max-height: 340px;
  overflow-y: auto;
  min-width: 200px;

  /* The menu teleports to `body`, outside the `#app` the kiosk scale transforms,
     so it has to re-apply that scale itself: without it the list renders in
     screen px while the trigger renders in app px x ui_scale, and the two
     typographies disagree by exactly that factor. Origin top-left leaves the box
     anchored on the viewport coordinates calculateDropdownDirection() computes. */
  transform: scale(var(--ui-scale, 1));
  transform-origin: top left;
}

/* Size: small — the menu carries the trigger's metrics, not the base ones. */
.dropdown-menu--small .dropdown-item {
  padding: var(--space-02) var(--space-03);
}

.dropdown-menu--small .dropdown-item::after {
  left: var(--space-03);
  right: var(--space-03);
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

/* Transition animations.

   Every state below restates the scale: `transform` is one property, so a
   keyframe that named only the translation would drop the kiosk scale for the
   duration of the animation and snap it back at the end. The translation is
   written after the scale on purpose — it then reads in the menu's own space,
   like its padding. No `transform-origin` here either: a translation is
   origin-independent, so the origins this block used to set were inert, and an
   origin other than the top-left one `.dropdown-menu` sets would now displace
   the scaled box mid-animation. */
.dropdown-menu-enter-active {
  transition:
    opacity var(--transition-fast),
    transform var(--transition-fast);
}

.dropdown-menu-leave-active {
  transition:
    opacity var(--transition-fast-leave),
    transform var(--transition-fast-leave);
}

.dropdown-menu-enter-from {
  opacity: 0;
  transform: scale(var(--ui-scale, 1)) translateY(-8px);
}

.dropdown-menu.open-upward.dropdown-menu-enter-from {
  opacity: 0;
  transform: scale(var(--ui-scale, 1)) translateY(8px);
}

.dropdown-menu-leave-to {
  opacity: 0;
  transform: scale(var(--ui-scale, 1)) translateY(-8px);
}

.dropdown-menu.open-upward.dropdown-menu-leave-to {
  opacity: 0;
  transform: scale(var(--ui-scale, 1)) translateY(8px);
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .dropdown-trigger--small {
    height: 34px;
  }

  /* Restore the base trigger padding on the minimal variant for mobile */
  .dropdown-trigger--minimal {
    padding: var(--space-03) var(--space-04);
  }
}
</style>
