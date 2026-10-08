<!-- frontend/src/components/ui/Badge.vue -->
<!-- A short status beside a row's title: a pill tinted by its tone, with a dot
     when the tone is a state rather than a plain fact. -->
<template>
  <span class="badge text-mono-small" :class="[`badge--${tone}`, { 'badge--pulse': pulse }]">
    <span v-if="tone !== 'neutral'" class="badge__dot" aria-hidden="true"></span>
    <slot />
  </span>
</template>

<script setup>
defineProps({
  // 'neutral' says a fact (a crossover at 80 Hz); the others a state.
  tone: {
    type: String,
    default: 'neutral',
    validator: (value) => ['neutral', 'success', 'warning', 'error', 'brand'].includes(value)
  },
  // The state is in progress (connecting): the dot breathes.
  pulse: {
    type: Boolean,
    default: false
  }
});
</script>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  height: 26px;
  padding: 0 10px 0 var(--space-02);
  border-radius: var(--radius-full);
  white-space: nowrap;
}

.badge--neutral {
  padding-left: 10px;
  background: var(--color-fill-faint);
  color: var(--color-text-secondary);
}

.badge--success {
  background: var(--color-success-subtle);
  color: var(--color-success);
}

.badge--warning {
  background: var(--color-warning-subtle);
  color: var(--color-warning);
}

.badge--error {
  background: var(--color-error-subtle);
  color: var(--color-error);
}

.badge--brand {
  background: var(--color-brand-subtle);
  color: var(--color-brand);
}

.badge__dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: currentColor;
}

.badge--pulse .badge__dot {
  animation: badge-pulse 1.4s var(--easeInOutCubic) infinite;
}

@keyframes badge-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.35; transform: scale(0.7); }
}

@media (prefers-reduced-motion: reduce) {
  .badge--pulse .badge__dot {
    animation: none;
  }
}
</style>
