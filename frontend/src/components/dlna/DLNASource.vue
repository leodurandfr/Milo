<!-- DLNASource.vue - DLNA / UPnP renderer player -->
<template>
  <AudioPlayerFull source="dlna" :showControls="false" />
</template>

<script setup>
import AudioPlayerFull from '@/components/audio/AudioPlayerFull.vue';
</script>
