<!-- frontend/src/components/settings/ToggleSection.vue -->
<!-- Reusable settings section with toggle in header and optional expand/collapse content -->
<template>
  <SettingsSection>
    <template #header>
      <div class="toggle-section-header">
        <div class="toggle-section-header__text">
          <h2 class="heading-2" :class="{ 'toggle-section-active': enabled && hasContent }">{{ title }}</h2>
          <p v-if="description" class="text-mono toggle-section-header__description">{{ description }}</p>
        </div>
        <Toggle :model-value="enabled" @change="$emit('change', $event)" />
      </div>
    </template>

    <Transition v-if="hasContent" name="expand">
      <div v-if="enabled">
        <slot />
      </div>
    </Transition>
  </SettingsSection>
</template>

<script setup>
import { computed, useSlots } from 'vue';
import Toggle from '@/components/ui/Toggle.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';

defineProps({
  title: { type: String, required: true },
  description: { type: String, default: '' },
  enabled: { type: Boolean, required: true }
});

defineEmits(['change']);

const slots = useSlots();
const hasContent = computed(() => !!slots.default);
</script>

<style scoped>
.toggle-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-04);
}

.toggle-section-header .heading-2 {
  transition: transform 300ms ease;
}

.toggle-section-header .heading-2.toggle-section-active {
  transform: translateY(-8px);
}

.toggle-section-header__description {
  color: var(--color-text-secondary);
  margin-top: var(--space-02);
}

/* Expand transition */
.expand-enter-active {
  transition: all 400ms ease;
  overflow: hidden;
}

.expand-leave-active {
  transition: all 300ms ease;
  overflow: hidden;
}

.expand-enter-from,
.expand-leave-to {
  opacity: 0;
  max-height: 0;
  margin-top: 0;
}

.expand-enter-to,
.expand-leave-from {
  opacity: 1;
  max-height: 300px;
}

/* Mobile: description takes full width */
@media (max-aspect-ratio: 4/3) {
  .toggle-section-header__description {
    flex-basis: 100%;
  }
}
</style>
