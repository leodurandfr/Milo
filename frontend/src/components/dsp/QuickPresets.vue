<!-- frontend/src/components/dsp/QuickPresets.vue -->
<!-- Quick preset buttons for common EQ configurations -->
<template>
  <div class="quick-presets" :class="{ mobile: isMobile }">
    <button
      v-for="preset in quickPresets"
      :key="preset.id"
      v-press.light
      class="quick-preset-btn"
      :class="{ active: activeQuickPreset === preset.id }"
      :disabled="disabled"
      @click="applyPreset(preset)"
    >
      {{ preset.label }}
    </button>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';
import { useI18n } from '@/services/i18n';

const { t } = useI18n();

const props = defineProps({
  disabled: {
    type: Boolean,
    default: false
  },
  isMobile: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['apply']);

const activeQuickPreset = ref(null);

// Predefined quick presets with gain values for 10 EQ bands
// Bands: 31Hz, 63Hz, 125Hz, 250Hz, 500Hz, 1kHz, 2kHz, 4kHz, 8kHz, 16kHz
const quickPresets = computed(() => [
  {
    id: 'flat',
    label: t('dsp.quickPresets.flat', 'Flat'),
    gains: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
  },
  {
    id: 'bass_boost',
    label: t('dsp.quickPresets.bassBoost', 'Bass+'),
    gains: [6, 5, 3, 1, 0, 0, 0, 0, 0, 0]
  },
  {
    id: 'vocal',
    label: t('dsp.quickPresets.vocal', 'Vocal'),
    gains: [-2, -1, 0, 1, 2, 3, 2, 1, 0, 0]
  },
  {
    id: 'night',
    label: t('dsp.quickPresets.nightMode', 'Night'),
    gains: [-4, -2, 0, 1, 1, 1, 0, -1, -2, -3]
  }
]);

function applyPreset(preset) {
  activeQuickPreset.value = preset.id;
  emit('apply', preset.gains);
}

// Clear active state when filters change externally
function clearActive() {
  activeQuickPreset.value = null;
}

defineExpose({ clearActive });
</script>

<style scoped>
.quick-presets {
  display: flex;
  gap: var(--space-02);
  padding: var(--space-02);
  background: var(--color-background-neutral);
  border-radius: var(--radius-05);
  overflow-x: auto;
}

.quick-preset-btn {
  flex-shrink: 0;
  padding: var(--space-02) var(--space-03);
  border: none;
  border-radius: var(--radius-04);
  background: var(--color-background-strong);
  color: var(--color-text-secondary);
  font-family: 'Space Mono Regular', monospace;
  font-size: 13px;
  cursor: pointer;
  transition: all var(--transition-fast), var(--transition-press);
  white-space: nowrap;
}

.quick-preset-btn:hover:not(:disabled) {
  background: var(--color-border);
  color: var(--color-text);
}

.quick-preset-btn.active {
  background: var(--color-brand);
  color: var(--color-text-inverse);
}

.quick-preset-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Mobile adjustments */
.quick-presets.mobile {
  padding: var(--space-01);
  gap: var(--space-01);
}

.quick-presets.mobile .quick-preset-btn {
  padding: var(--space-01) var(--space-02);
  font-size: 12px;
}

/* Scrollbar styling */
.quick-presets::-webkit-scrollbar {
  height: 4px;
}

.quick-presets::-webkit-scrollbar-thumb {
  background: var(--color-border);
  border-radius: 2px;
}
</style>
