<!-- frontend/src/components/dsp/ParametricEQ.vue -->
<!-- 10+ band parametric equalizer display -->
<template>
  <div class="parametric-eq" :class="{ 'mobile': isMobile }">
    <div class="eq-bands" :class="{ 'loading': !filtersLoaded }">
      <EQBand
        v-for="filter in filters"
        :key="filter.id"
        :id="filter.id"
        :freq="filter.freq"
        :gain="filter.gain"
        :q="filter.q"
        :type="filter.type"
        :display-name="filter.displayName"
        :orientation="bandOrientation"
        :disabled="disabled || !filtersLoaded"
        :compact="filters.length > 10"
        @update:gain="handleGainUpdate(filter.id, $event)"
        @change="handleBandChange(filter.id, $event)"
      />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import EQBand from './EQBand.vue';

const props = defineProps({
  filters: {
    type: Array,
    required: true
  },
  filtersLoaded: {
    type: Boolean,
    default: false
  },
  disabled: {
    type: Boolean,
    default: false
  },
  isMobile: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['update:filter', 'change']);

// Band orientation based on mobile status
const bandOrientation = computed(() => props.isMobile ? 'horizontal' : 'vertical');

// === HANDLERS ===
function handleGainUpdate(filterId, value) {
  emit('update:filter', { id: filterId, field: 'gain', value });
}

function handleBandChange(filterId, { field, value }) {
  emit('change', { id: filterId, field, value });
}
</script>

<style scoped>
.parametric-eq {
  width: 100%;
}

.eq-bands {
  display: flex;
  justify-content: space-between;
  gap: var(--space-02);
  overflow-x: auto;
  transition: opacity var(--transition-normal);
}

.eq-bands.loading {
  opacity: 0.5;
}

/* Mobile layout */
.parametric-eq.mobile .eq-bands {
  flex-direction: column;
  gap: var(--space-03);
}

/* Scrollbar styling */
.eq-bands::-webkit-scrollbar {
  height: 4px;
}

.eq-bands::-webkit-scrollbar-track {
  background: transparent;
}

.eq-bands::-webkit-scrollbar-thumb {
  background: var(--color-border);
  border-radius: 2px;
}

.eq-bands::-webkit-scrollbar-thumb:hover {
  background: var(--color-text-light);
}
</style>
