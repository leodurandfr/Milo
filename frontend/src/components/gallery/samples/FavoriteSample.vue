<!-- frontend/src/components/gallery/samples/FavoriteSample.vue -->
<!--
  Radio's favorite at the end of the transport, inked the way RadioSource inks
  it: from the `ink` the transport's end slot hands over, so it takes the
  plate's tone on the full player and the card's on the bar.
-->
<template>
  <IconButton icon="heart" variant="ghost" size="small" class="transport-secondary-round" :color="ink.strong" />
</template>

<script setup>
import IconButton from '@/components/ui/IconButton.vue';

defineProps({
  ink: { type: Object, required: true }
});
</script>
