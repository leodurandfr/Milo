<!-- frontend/src/components/settings/categories/RemoteStatusSection.vue -->
<!-- Shared status card for the connected/paired remotes: status dot + label, an
     optional action button (BT scan) and the unpair action, then the volume-step slider.
     Used by BT (both activated states) and IR (paired state only). -->

<template>
  <SectionCard>
    <template #header>
      <SectionHeader>
        <template #title>
          <h3 class="heading-3">
            <span class="remote-status">
              <span class="remote-status__dot" :class="{ 'is-ok': ok }" />
              <span class="remote-status__label"><slot name="status">{{ statusLabel }}</slot></span>
            </span>
          </h3>
        </template>
        <template v-if="ctaLabel || showUnpair" #actions>
          <Button
            v-if="ctaLabel"
            variant="brand"
            size="small"
            :loading="ctaLoading"
            :disabled="ctaDisabled"
            @click="ctaClick"
          >
            {{ ctaLabel }}
          </Button>
          <Button
            v-if="showUnpair"
            variant="tinted"
            size="small"
            :loading="unpairLoading"
            :disabled="unpairLoading"
            @click="unpairClick"
          >
            {{ unpairLabel }}
          </Button>
        </template>
      </SectionHeader>
    </template>

    <RangeSlider :label="stepLabel"
      :model-value="modelValue"
      :min="1" :max="6" :step="1" ticks
      unit="dB"
      @update:model-value="$emit('update:modelValue', $event)"
      @change="$emit('step-change', $event)"
    />
  </SectionCard>
</template>

<script setup>
import RangeSlider from '@/components/ui/RangeSlider.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import SectionHeader from '@/components/ui/SectionHeader.vue';
import Button from '@/components/ui/Button.vue';

defineProps({
  ok: { type: Boolean, default: false },
  statusLabel: { type: String, default: '' },
  ctaLabel: { type: String, default: null },
  ctaLoading: { type: Boolean, default: false },
  ctaDisabled: { type: Boolean, default: false },
  ctaClick: { type: Function, default: null },
  modelValue: { type: Number, required: true },
  stepLabel: { type: String, default: '' },
  showUnpair: { type: Boolean, default: false },
  unpairLabel: { type: String, default: '' },
  unpairLoading: { type: Boolean, default: false },
  unpairClick: { type: Function, default: null }
});

defineEmits(['update:modelValue', 'step-change']);
</script>

<style scoped>
.remote-status {
  display: inline-flex;
  align-items: center;
  gap: var(--space-02);
  vertical-align: top;
}

.remote-status__dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-error);
}

.remote-status__dot.is-ok {
  background: var(--color-success);
}
</style>
