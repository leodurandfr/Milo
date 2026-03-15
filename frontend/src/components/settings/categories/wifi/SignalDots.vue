<!-- Signal strength indicator as 4 dots -->
<template>
  <div class="signal-dots" :title="signal != null ? signal + '%' : ''">
    <span v-for="i in 4" :key="i" class="signal-dot" :class="{ 'signal-dot--filled': i <= filledDots }" />
  </div>
</template>

<script setup>
import { computed } from 'vue';

const props = defineProps({
  signal: {
    type: Number,
    default: null
  }
});

const filledDots = computed(() => {
  if (props.signal == null) return 0;
  if (props.signal >= 75) return 4;
  if (props.signal >= 50) return 3;
  if (props.signal >= 25) return 2;
  return 1;
});
</script>

<style scoped>
.signal-dots {
  display: flex;
  align-items: center;
  gap: 3px;
}

.signal-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-background-medium-16);
  transition: background-color var(--transition-fast);
}

.signal-dot--filled {
  background: var(--color-text);
}
</style>
