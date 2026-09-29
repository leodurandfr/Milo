<!-- frontend/src/components/gallery/samples/HeaderActionSample.vue -->
<!--
  The one action a settings section header carries: a small outline Button.

  A slot choice is rendered with props only, and a Button's label is its slot,
  so passing `Button` directly would draw an empty pill.
-->
<template>
  <Button variant="outline" size="small">Add</Button>
</template>

<script setup>
import Button from '@/components/ui/Button.vue';
</script>
