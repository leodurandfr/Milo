<!-- frontend/src/components/ui/ToggleSection.vue -->
<!-- Reusable settings section with toggle in header and optional expand/collapse content -->
<template>
  <SettingsSection :class="{ 'toggle-section--has-content': hasContent }">
    <template #header>
      <div class="toggle-section-header">
        <h2 class="heading-2">
          <slot name="title">{{ title }}</slot>
        </h2>
        <div v-if="slots.actions" class="toggle-section-header__actions">
          <slot name="actions" />
        </div>
        <Toggle :model-value="enabled" @change="handleToggle" />
      </div>
    </template>

    <div v-if="hasContent" ref="expandRef" class="toggle-section-expand" :class="{ 'is-open': enabled, 'no-transition': skipInitialTransition }">
      <div class="toggle-section-expand__inner">
        <slot />
      </div>
    </div>
  </SettingsSection>
</template>

<script setup>
import { ref, computed, useSlots, inject, onMounted, onUnmounted } from 'vue';
import Toggle from '@/components/ui/Toggle.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';

const props = defineProps({
  title: { type: String, default: '' },
  enabled: { type: Boolean, required: true }
});

const emit = defineEmits(['change']);

const slots = useSlots();
const hasContent = computed(() => !!slots.default);

const expandRef = ref(null);
const skipInitialTransition = ref(true);
const requestHeightDelta = inject('modalRequestHeightDelta', null);
const contentInnerRef = inject('modalContentInnerRef', null);

// Suppress CSS grid transition on initial mount so the content appears at full
// height immediately. Without this, the 0fr→1fr grid animation fires when the
// page enters the DOM, and the ResizeObserver follows it frame-by-frame instead
// of letting the Modal apply a single spring.
let rafId = null;
onMounted(() => {
  rafId = requestAnimationFrame(() => {
    rafId = requestAnimationFrame(() => {
      skipInitialTransition.value = false;
      rafId = null;
    });
  });
});

onUnmounted(() => {
  if (rafId) cancelAnimationFrame(rafId);
});

/**
 * Measure exact height delta by temporarily snapping to target state,
 * then revert and let requestHeightDelta set the container target.
 * Modal springs from old→new while the CSS grid animates independently.
 */
function handleToggle(newEnabled) {
  if (requestHeightDelta && expandRef.value && contentInnerRef?.value) {
    const el = expandRef.value;
    const before = contentInnerRef.value.offsetHeight;

    // Snap to target state (no transition) to measure final height
    el.style.transition = 'none';
    el.classList.toggle('is-open', newEnabled);
    el.offsetHeight;
    const after = contentInnerRef.value.offsetHeight;

    // Revert to current state
    el.classList.toggle('is-open', !newEnabled);
    el.offsetHeight;
    el.style.transition = '';

    requestHeightDelta(after - before, 200); // must match --transition-fast
  }
  emit('change', newEnabled);
}
</script>

<style scoped>
.toggle-section-header {
  display: flex;
  align-items: center;
  gap: var(--space-04);
}

.toggle-section-header > .heading-2 {
  margin-right: auto;
  min-width: 0;
}

.toggle-section-header__actions {
  display: flex;
  align-items: center;
  gap: var(--space-02);
}

/* Suppress transition on initial mount (prevents 0fr→1fr grid animation on page entry) */
.toggle-section-expand.no-transition {
  transition: none;
}

/* Expand/collapse via CSS grid — Modal's ResizeObserver tracks the height change naturally. */
.toggle-section-expand {
  display: grid;
  grid-template-rows: 0fr;
  opacity: 0;
  margin-top: calc(-1 * var(--space-04));
  transition:
    grid-template-rows var(--transition-fast),
    opacity var(--transition-fast),
    margin-top var(--transition-fast);
}

.toggle-section-expand.is-open {
  grid-template-rows: 1fr;
  opacity: 1;
  margin-top: 0;
}

/* Card clips expanding content at its border-radius edge. */
.settings-section.toggle-section--has-content {
  overflow: hidden;
}

.toggle-section-expand__inner {
  min-height: 0;  /* Fixes iOS collapse */
  min-width: 0;   /* Allows children with long unbreakable text to ellipsize instead of overflowing */
}

</style>
