<!-- AirPlaySource.vue - AirPlay 2 player -->
<template>
  <AudioPlayerFull source="airplay" :showControls="false" :showProgress="true" />
</template>

<script setup>
import AudioPlayerFull from '@/components/audio/AudioPlayerFull.vue';
</script>
