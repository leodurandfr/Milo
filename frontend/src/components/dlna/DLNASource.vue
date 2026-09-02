<!-- DLNASource.vue - DLNA/UPnP renderer player (Family B: passive receiver).
     Same shape as AirPlay and Qobuz: the read-only progress bar is fed by the
     UPnP bridge's polled position (rate-limited to 30 s, interpolated locally),
     while transport stays in the sender's app. -->
<template>
  <AudioPlayerFull source="dlna" :showControls="false" :showProgress="true" />
</template>

<script setup>
import AudioPlayerFull from '@/components/audio/AudioPlayerFull.vue';
</script>
