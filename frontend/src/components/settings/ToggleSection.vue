<!-- frontend/src/components/settings/ToggleSection.vue -->
<!-- Reusable settings section with toggle in header and optional expand/collapse content -->
<template>
  <SettingsSection :class="{ 'toggle-section--has-content': hasContent }">
    <template #header>
      <div class="toggle-section-header">
        <h2 class="heading-2">{{ title }}</h2>
        <Toggle :model-value="enabled" @change="handleToggle" />
      </div>
    </template>

    <div v-if="hasContent" class="toggle-section-expand" :class="{ 'is-open': enabled }">
      <div ref="innerRef" class="toggle-section-expand__inner">
        <slot />
      </div>
    </div>
  </SettingsSection>
</template>

<script setup>
import { ref, computed, useSlots, inject } from 'vue';
import Toggle from '@/components/ui/Toggle.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';

const props = defineProps({
  title: { type: String, required: true },
  enabled: { type: Boolean, required: true }
});

const emit = defineEmits(['change']);

const slots = useSlots();
const hasContent = computed(() => !!slots.default);

// Ref to measure inner content height
const innerRef = ref(null);

// Inject Modal's height request function (null if not in Modal)
const requestHeightDelta = inject('modalRequestHeightDelta', null);

/**
 * Handle toggle with pre-announced height change.
 * Notifies Modal of the height delta BEFORE the CSS animation starts,
 * so Modal can animate smoothly to the target height.
 */
function handleToggle(newEnabled) {
  if (requestHeightDelta && innerRef.value) {
    // Smart requestHeightDelta: auto-detects when modal is at max height
    // and skips lock to avoid wrong predictions when content overflows
    const contentHeight = innerRef.value.scrollHeight;
    const delta = newEnabled ? contentHeight : -contentHeight;
    requestHeightDelta(delta);
  }
  emit('change', newEnabled);
}
</script>

<style scoped>
.toggle-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-04);
}

/* Expand/collapse via CSS grid with pre-announced height change to Modal.
   Modal is notified of the target height BEFORE animation starts via requestHeightDelta(),
   so Modal animates smoothly while this content animates visually. */
.toggle-section-expand {
  display: grid;
  grid-template-rows: 0fr;
  opacity: 0;
  margin-top: calc(-1 * var(--space-04));
  transition:
    grid-template-rows var(--transition-normal),
    opacity var(--transition-normal),
    margin-top var(--transition-normal);
}

.toggle-section-expand.is-open {
  grid-template-rows: 1fr;
  opacity: 1;
  margin-top: 0;
}

/* Remove parent's bottom padding — moved inside __inner for smooth collapse */
.settings-section.toggle-section--has-content {
  padding-bottom: 0;
}

.toggle-section-expand__inner {
  overflow: hidden;
  min-height: 0;  /* Fixes iOS collapse */
  padding-bottom: var(--space-05-fixed);
}

</style>
