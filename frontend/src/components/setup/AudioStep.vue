<!-- frontend/src/components/setup/AudioStep.vue -->
<template>
  <div class="audio-step">
    <!-- Grouped audio cards by category -->
    <template v-for="group in groupedCards" :key="group.category">
      <span class="text-mono audio-step__category">
        {{ categoryLabel(group.category) }}
      </span>
      <div class="audio-step__list">
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

const { t } = useI18n();

const props = defineProps({
  modelValue: {
    type: String,
    default: 'none',
  },
  audioCards: {
    type: Array,
    default: () => [],
  },
});

const emit = defineEmits(['update:modelValue']);

const categoryLabelMap = {
  amplifier: 'setup.audio.amplifiers',
  dac: 'setup.audio.dacs',
  speaker: 'setup.audio.speakers',
};

function categoryLabel(category) {
  return t(categoryLabelMap[category] || 'setup.audio.other');
}

function cardLabel(card) {
  return card.value === 'none' ? t('setup.audio.none') : card.label;
}

const categoryOrder = ['amplifier', 'dac', 'speaker'];

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
</style>
