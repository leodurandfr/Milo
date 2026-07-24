<!-- QobuzPlayer.vue - Qobuz Connect player (Family B: passive receiver, no on-device controls).
     Same layout as AirPlay plus a read-only progress bar: qobuz-proxy reports
     position/duration on its status poll (Milo's patch), but seeking stays in
     the Qobuz app — there is no local control channel. -->
<template>
  <AudioPlayerFull source="qobuz" :showControls="false" :showProgress="true" />
</template>

<script setup>
import AudioPlayerFull from '@/components/audio/AudioPlayerFull.vue';
</script>
