<!-- QobuzSource.vue - Qobuz Connect player (Family B: passive receiver, no on-device controls).
     Same layout as AirPlay: the proxy reports position and duration, so the
     bar is drawn, read-only — the state lists no command for Qobuz, seeking
     stays in the Qobuz app. -->
<template>
  <AudioPlayerFull source="qobuz" />
</template>

<script setup>
import AudioPlayerFull from '@/components/audio/AudioPlayerFull.vue';
</script>
