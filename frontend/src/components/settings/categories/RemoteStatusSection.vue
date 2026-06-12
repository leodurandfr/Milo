<!-- frontend/src/components/settings/categories/RemoteStatusSection.vue -->
<!-- Shared status card for the connected/paired remotes: status dot + label, an
     optional action button (BT scan), the volume-step slider and the unpair action.
     Used by BT (both activated states) and IR (paired state only). -->

<template>
  <SettingsSection>
    <template #header>
      <div class="remote-header">
        <h3 class="remote-header__title heading-3">
          <span class="remote-status">
            <span class="remote-status__dot" :class="{ 'is-ok': ok }" />
            <span class="remote-status__label"><slot name="status">{{ statusLabel }}</slot></span>
          </span>
        </h3>
        <Button
          v-if="ctaLabel"
          :variant="ctaVariant"
          size="small"
          :loading="ctaLoading"
          :disabled="ctaDisabled"
          @click="ctaClick"
        >
          {{ ctaLabel }}
        </Button>
        <Button
          v-if="showUnpair"
          class="unpair-button unpair-button--desktop"
          variant="background-strong"
          size="small"
          :loading="unpairLoading"
          :disabled="unpairLoading"
          @click="unpairClick"
        >
          {{ unpairLabel }}
        </Button>
      </div>
    </template>

    <SettingItem :label="stepLabel">
      <RangeSlider
        :model-value="modelValue"
        :min="1" :max="6" :step="1"
        value-unit=" dB"
        @update:model-value="$emit('update:modelValue', $event)"
        @change="$emit('step-change', $event)"
      />
    </SettingItem>

    <Button
      v-if="showUnpair"
      class="unpair-button unpair-button--mobile"
      variant="background-strong"
      size="small"
      :loading="unpairLoading"
      :disabled="unpairLoading"
      @click="unpairClick"
    >
      {{ unpairLabel }}
    </Button>
  </SettingsSection>
</template>

<script setup>
import RangeSlider from '@/components/ui/RangeSlider.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import Button from '@/components/ui/Button.vue';

defineProps({
  ok: { type: Boolean, default: false },
  statusLabel: { type: String, default: '' },
  ctaLabel: { type: String, default: null },
  ctaVariant: { type: String, default: 'brand' },
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
.remote-header {
  display: flex;
  align-items: center;
  gap: var(--space-04);
}

.remote-header__title {
  margin-right: auto;
  min-width: 0;
}

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

/* Desktop: unpair lives in the header next to the action button.
   Narrow/touchscreen: full-width below the slider so the header doesn't crowd. */
.unpair-button--mobile {
  display: none;
}

@media (max-aspect-ratio: 4/3) {
  .unpair-button--desktop {
    display: none;
  }

  .unpair-button--mobile {
    display: flex;
    width: 100%;
    margin-top: var(--space-04);
  }
}
</style>
