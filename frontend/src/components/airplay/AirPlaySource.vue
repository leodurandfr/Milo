<!-- AirPlaySource.vue - AirPlay 2 player (passive receiver: the transport is
     the sender's own, relayed — `controls` lists what it takes — and
     AudioPlayerFull draws the sender's name in its source bar). The bar is
     read-only (shairport-sync sends no seek) and appears while the sender
     reports a position; a Mac's system audio reports none and publishes no
     pause, so it has no bar to run on through a paused track. -->
<template>
  <AudioPlayerFull source="airplay" />
</template>

<script setup>
import AudioPlayerFull from '@/components/audio/AudioPlayerFull.vue';
</script>
