<!-- AirPlaySource.vue - AirPlay 2 player (passive receiver: no transport on the
     unit, so AudioPlayerFull draws the sender's name in its source bar). The
     bar appears only while a Buffered sender (iPhone Music) reports a position;
     a Realtime one (a Mac's system audio) reports none and publishes no pause,
     so it has no bar to run on through a paused track. -->
<template>
  <AudioPlayerFull source="airplay" />
</template>

<script setup>
import AudioPlayerFull from '@/components/audio/AudioPlayerFull.vue';
</script>
