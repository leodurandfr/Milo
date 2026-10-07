<!-- frontend/src/components/gallery/samples/FavoriteSample.vue -->
<!--
  Radio's favorite at the end of the transport, drawn the way RadioSource draws
  it: a ghost, which the transport's end slot draws in the box a toggle takes.
-->
<template>
  <IconButton icon="heart" variant="ghost" size="medium" />
</template>

<script setup>
import IconButton from '@/components/ui/IconButton.vue';
</script>
