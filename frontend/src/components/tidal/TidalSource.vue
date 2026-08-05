<template>
  <!-- Transport controls, like Spotify: the Tidal app hands over a queue and
       the tisoc controller socket accepts play/pause/next/previous. The
       progress bar is read-only though — the protocol exposes no seek. -->
  <AudioPlayerFull source="tidal" :seekable="false" />
</template>

<script setup>
import AudioPlayerFull from '@/components/audio/AudioPlayerFull.vue';
</script>
