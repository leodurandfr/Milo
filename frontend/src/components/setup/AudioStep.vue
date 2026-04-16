<!-- frontend/src/components/setup/AudioStep.vue -->
<template>
  <div class="audio-step">
    <!-- Grouped audio cards by category -->
    <template v-for="group in groupedCards" :key="group.category">
      <span class="text-mono audio-step__category">
        {{ categoryLabel(group.category) }}
      </span>
      <div class="audio-step__list">
        <!-- Volume management toggle (first item in DACs list) -->
        <button v-if="group.category === 'dac'" type="button" class="audio-step__volume-control"
          :disabled="!isDacSelected" @click="isDacSelected && emit('update:volumeControl', !volumeControl)">
          <span class="heading-3" :class="{ 'audio-step__volume-label--disabled': !isDacSelected }">
            {{ t('setup.audio.volumeManagement') }}
          </span>
          <Toggle :model-value="isDacSelected && volumeControl" size="compact" :disabled="!isDacSelected" />
        </button>

        <ListItemButton
          v-for="card in group.cards"
          :key="card.value"
          :title="card.label"
          variant="background"
          action="radio"
          :model-value="modelValue === card.value"
          @click="emit('update:modelValue', card.value)"
        />
      </div>
    </template>

    <!-- Ungrouped cards (e.g. "No audio card") -->
    <div v-if="ungroupedCards.length" class="audio-step__list">
      <ListItemButton
        v-for="card in ungroupedCards"
        :key="card.value"
        :title="cardLabel(card)"
        variant="background"
        action="radio"
        :model-value="modelValue === card.value"
        @click="emit('update:modelValue', card.value)"
      />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import Toggle from '@/components/ui/Toggle.vue';

const { t } = useI18n();

const props = defineProps({
  modelValue: {
    type: String,
    default: 'none',
  },
  volumeControl: {
    type: Boolean,
    default: true,
  },
  audioCards: {
    type: Array,
    default: () => [],
  },
});

const emit = defineEmits(['update:modelValue', 'update:volumeControl']);

const isDacSelected = computed(() => {
  const card = props.audioCards.find(c => c.value === props.modelValue);
  return card?.category === 'dac';
});

const categoryLabelMap = {
  amplifier: 'setup.audio.amplifiers',
  dac: 'setup.audio.dacs',
};

function categoryLabel(category) {
  return t(categoryLabelMap[category] || 'setup.audio.other');
}

function cardLabel(card) {
  return card.value === 'none' ? t('setup.audio.none') : card.label;
}

const categoryOrder = ['amplifier', 'dac'];

const groupedCards = computed(() => {
  const groups = {};
  for (const card of props.audioCards) {
    if (card.category) {
      if (!groups[card.category]) {
        groups[card.category] = [];
      }
      groups[card.category].push(card);
    }
  }

  const result = [];
  for (const cat of categoryOrder) {
    if (groups[cat]) {
      result.push({ category: cat, cards: groups[cat] });
    }
  }
  // Any remaining categories not in the predefined order
  for (const [cat, cards] of Object.entries(groups)) {
    if (!categoryOrder.includes(cat)) {
      result.push({ category: cat, cards });
    }
  }
  return result;
});

const ungroupedCards = computed(() =>
  props.audioCards
    .filter(card => !card.category)
    .sort((a, b) => (a.value === 'none') - (b.value === 'none'))
);
</script>

<style scoped>
.audio-step {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.audio-step__category {
  color: var(--color-text-secondary);
}

.audio-step__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

/* Extra spacing between groups (amplifiers → dacs → ungrouped) */
.audio-step__list + .audio-step__category,
.audio-step__list + .audio-step__list {
  margin-top: var(--space-04);
}

/* Volume control: plain text + toggle, no card styling */
.audio-step__volume-control {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: var(--space-02) 0;
  background: none;
  border: none;
  cursor: pointer;
}

.audio-step__volume-control:disabled {
  cursor: not-allowed;
}

.audio-step__volume-control .heading-3 {
  color: var(--color-text);
  transition: color var(--transition-fast);
}

.audio-step__volume-label--disabled {
  color: var(--color-text-light) !important;
}

/* Prevent double-toggle when clicking directly on Toggle */
.audio-step__volume-control :deep(.toggle) {
  pointer-events: none;
}

/* Disabled toggle: no opacity, use muted track color instead */
.audio-step__volume-control:disabled :deep(.toggle) {
  opacity: 1;
}

.audio-step__volume-control:disabled :deep(.slider) {
  background-color: var(--color-background-medium-16);
}
</style>
