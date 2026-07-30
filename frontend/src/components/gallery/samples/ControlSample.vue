<!-- frontend/src/components/gallery/samples/ControlSample.vue -->
<!--
  One working control, for the slots whose whole job is to hold one.

  A slot choice is rendered with fixed props, so passing `RangeSlider` directly
  would put a slider on screen whose handle cannot move — the failure mode this
  page exists to catch, reproduced by the page itself. This owns the value
  instead, so what a reader drags behaves like the real thing.
-->
<template>
  <RangeSlider v-model="value" value-unit="%" />
</template>

<script setup>
import { ref } from 'vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';

const value = ref(35);
</script>
