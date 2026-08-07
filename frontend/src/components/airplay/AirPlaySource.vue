<!-- AirPlaySource.vue - AirPlay 2 player.
     No progress bar, unlike the other two receivers: an AirPlay sender that
     pauses announces nothing shairport-sync can pass on, so is_playing stays
     true and the bar ran on through a paused track. The sender's own screen
     shows the position anyway. See the AirPlaySource docstring for the channels
     measured before giving up on detecting it. -->
<template>
  <AudioPlayerFull source="airplay" :showControls="false" :showProgress="false" />
</template>

<script setup>
import AudioPlayerFull from '@/components/audio/AudioPlayerFull.vue';
</script>
