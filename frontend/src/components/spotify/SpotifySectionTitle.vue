<template>
  <!-- A section's title; with more than its row shows, the way to all of it
       (Spotify's "Show all"), marked by a caret. -->
  <button v-if="linked" v-press type="button" class="section-title section-title--linked" @click="$emit('open')">
    <h2 class="heading-2">{{ title }}</h2>
    <SvgIcon name="caretRight" :size="24" class="section-caret" />
  </button>
  <h2 v-else class="section-title heading-2">{{ title }}</h2>
</template>

<script setup>
import SvgIcon from '@/components/ui/SvgIcon.vue';

defineProps({
  title: {
    type: String,
    required: true,
  },
  linked: {
    type: Boolean,
    default: false,
  },
});

defineEmits(['open']);
</script>

<style scoped>
.section-title {
  color: var(--color-text);
  margin: 0;
}

.section-title--linked {
  display: inline-flex;
  align-items: center;
  gap: var(--space-01);
  align-self: flex-start;
  padding: 0;
  border: none;
  background: transparent;
  cursor: pointer;
}

.section-title--linked h2 {
  margin: 0;
  color: inherit;
}

/* Centred on the title's lowercase rather than its capitals, which most of a
   title is: on the box's middle it read as riding high (2 px, measured). */
.section-caret {
  color: var(--color-text-tertiary);
  transform: translateY(2px);
}
</style>
