<!-- frontend/src/components/gallery/samples/TriggerSample.vue -->
<!--
  An IconButton wired to the Dropdown trigger slot's `toggle`, the way the
  Update Manager's version menu uses it. A fixed-props IconButton would open
  nothing.
-->
<template>
  <IconButton :icon="icon" variant="background-strong" size="small" :disabled="disabled" @click="toggle" />
</template>

<script setup>
import IconButton from '@/components/ui/IconButton.vue';

defineProps({
  toggle: { type: Function, required: true },
  disabled: { type: Boolean, default: false },
  icon: { type: String, default: 'clockCounterClockwise' }
});
</script>
