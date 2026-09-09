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
         wants to kick the phone off. So it is repeated here, with the card's
         own wording; without it the only way to end a session would be to
         leave the source entirely. On mobile the row is lifted onto the cover
         like CD's eject, which is why the plate changes with it. -->
    <template #action-buttons>
      <div class="action-buttons">
        <Button :variant="isMobile ? 'on-grey' : 'background-strong'" size="medium"
          :loading="unifiedStore.isDisconnecting('bluetooth')"
          :disabled="unifiedStore.isDisconnecting('bluetooth')"
          @click="unifiedStore.disconnectSource('bluetooth')">
          {{ unifiedStore.isDisconnecting('bluetooth') ? t('status.disconnecting') : t('status.disconnect') }}
        </Button>
      </div>
    </template>
  </AudioPlayerFull>
</template>

<script setup>
import { useI18n } from '@/services/i18n';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useIsMobile } from '@/composables/useIsMobile';

import AudioPlayerFull from '@/components/audio/AudioPlayerFull.vue';
import Button from '@/components/ui/Button.vue';

const { t } = useI18n();
const unifiedStore = useUnifiedAudioStore();
const { isMobile } = useIsMobile();
</script>

<style scoped>
.action-buttons {
  display: flex;
  justify-content: flex-end;
  flex-shrink: 0;
}

@media (max-aspect-ratio: 4/3) {
  /* Same anchor as CD's eject: absolute against .connect-player, so the row
     sits on the cover's top-right corner instead of below it. */
  .action-buttons {
    position: absolute;
    top: calc(max(var(--space-05), env(safe-area-inset-top, 0px)) + var(--space-04));
    left: calc(var(--space-05) + var(--space-04));
    right: calc(var(--space-05) + var(--space-04));
    z-index: 10;
  }
}
</style>
