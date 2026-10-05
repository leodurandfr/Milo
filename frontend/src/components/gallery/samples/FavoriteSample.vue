<!-- frontend/src/components/gallery/samples/FavoriteSample.vue -->
<!--
  Radio's favorite at the end of the transport, drawn the way RadioSource draws
  it: in the `variant` the transport's end slot hands over — the fill of the
  labelled button beside it, on the plate as on the bar's card.
-->
<template>
  <IconButton icon="heart" :variant="variant" size="medium" />
</template>

<script setup>
import IconButton from '@/components/ui/IconButton.vue';

defineProps({
  variant: { type: String, required: true }
});
</script>
