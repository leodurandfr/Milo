<!-- QobuzPlayer.vue - Qobuz Connect player (Family B: passive receiver, no on-device controls) -->
<template>
  <AudioPlayerFull source="qobuz" :showControls="false" />
</template>

<script setup>
import AudioPlayerFull from '@/components/audio/AudioPlayerFull.vue';
</script>
