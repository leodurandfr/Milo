<!-- frontend/src/components/ui/ToggleSection.vue -->
<!-- A SectionCard with a toggle in its header, expanding its content when on -->
<template>
  <SectionCard :description="description">
    <template #header>
      <div class="toggle-section-header">
        <component :is="`h${heading}`" :class="`heading-${heading}`">
          <slot name="title">{{ title }}</slot>
        </component>
        <div v-if="slots.actions" class="toggle-section-header__actions">
          <slot name="actions" />
        </div>
        <Toggle :model-value="enabled" :disabled="disabled" @change="emit('change', $event)" />
      </div>
    </template>

    <Collapse v-if="hasContent" :open="enabled">
      <slot />
    </Collapse>
  </SectionCard>
</template>

<script setup>
import { computed, useSlots } from 'vue';
import Toggle from '@/components/ui/Toggle.vue';
import Collapse from '@/components/ui/Collapse.vue';
import SectionCard from '@/components/ui/SectionCard.vue';

defineProps({
  title: { type: String, default: '' },
  description: { type: String, default: '' },
  enabled: { type: Boolean, required: true },
  disabled: { type: Boolean, default: false },
  heading: { type: [String, Number], default: 2, validator: (v) => ['2', '3', 2, 3].includes(v) }
});

const emit = defineEmits(['change']);

const slots = useSlots();
const hasContent = computed(() => !!slots.default);
</script>

<style scoped>
.toggle-section-header {
  display: flex;
  align-items: center;
  gap: var(--space-04);
}

.toggle-section-header > .heading-2,
.toggle-section-header > .heading-3 {
  margin-right: auto;
  min-width: 0;
}

.toggle-section-header__actions {
  display: flex;
  align-items: center;
  gap: var(--space-02);
}

</style>
