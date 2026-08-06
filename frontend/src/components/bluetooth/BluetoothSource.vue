<!-- BluetoothSource.vue - Bluetooth player (wrapper around AudioPlayerFull) -->
<template>
  <!-- Transport controls, like Tidal: the phone hands over a track and BlueZ's
       AVRCP controller accepts Play/Pause/Next/Previous. The progress bar is
       read-only — AVRCP has no seek, only hold-style fast-forward. No cover
       comes over the link either (AVRCP carries it on a separate OBEX channel
       BlueZ gives no client for), so the one in the artwork slot was resolved
       from the track text by shared/artwork_resolver.py and arrives in the same
       album_art_url field a source with real artwork fills — asynchronously,
       and a miss leaves the slot on its source glyph. -->
  <AudioPlayerFull source="bluetooth" :seekable="false">
    <!-- The disconnect CTA lives on the status card, which this player replaces
         the moment the sender publishes a track — i.e. exactly when a user
         wants to kick the phone off. So it is repeated here; without it the
         only way to end a session would be to leave the source entirely. -->
    <template #action-buttons>
      <div class="action-buttons">
        <IconButton icon="close" :variant="isMobile ? 'on-grey' : 'background-strong'" size="medium"
          :loading="unifiedStore.isDisconnecting('bluetooth')"
          @click="unifiedStore.disconnectSource('bluetooth')" />
      </div>
    </template>
  </AudioPlayerFull>
</template>

<script setup>
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useIsMobile } from '@/composables/useIsMobile';

import AudioPlayerFull from '@/components/audio/AudioPlayerFull.vue';
import IconButton from '@/components/ui/IconButton.vue';

const unifiedStore = useUnifiedAudioStore();
const { isMobile } = useIsMobile();
</script>

<style scoped>
.action-buttons {
  display: flex;
  justify-content: flex-end;
  flex-shrink: 0;
}
</style>
