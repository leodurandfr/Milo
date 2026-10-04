<!-- BluetoothSource.vue - Bluetooth player (wrapper around AudioPlayerFull) -->
<template>
  <!-- Transport controls, like Tidal: the phone hands over a track and BlueZ's
       AVRCP controller accepts Play/Pause/Next/Previous. The progress bar is
       read-only — AVRCP has no seek, only hold-style fast-forward, so the state
       never lists one. No cover comes over the link either (AVRCP carries it
       on a separate OBEX channel BlueZ gives no client for), so the one in the
       artwork slot was resolved from the track text by
       shared/artwork_resolver.py and arrives in the same `artwork` field a
       source with real artwork fills — asynchronously, and a miss leaves the
       slot on its source glyph. -->
  <AudioPlayerFull source="bluetooth">
    <!-- The disconnect CTA lives on the status card, which this player replaces
         the moment the sender publishes a track — i.e. exactly when a user
         wants to kick the phone off. So it is repeated here, with the card's
         own wording, at the end of the player's top row; without it the only
         way to end a session would be to leave the source entirely. -->
    <template v-if="canDisconnect" #top-end>
      <Button variant="background-strong" size="medium"
        :loading="unifiedStore.isDisconnecting('bluetooth')"
        :disabled="unifiedStore.isDisconnecting('bluetooth')"
        @click="unifiedStore.disconnectSource('bluetooth')">
        {{ unifiedStore.isDisconnecting('bluetooth') ? t('status.disconnecting') : t('status.disconnect') }}
      </Button>
    </template>
  </AudioPlayerFull>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

import AudioPlayerFull from '@/components/audio/AudioPlayerFull.vue';
import Button from '@/components/ui/Button.vue';

const { t } = useI18n();
const unifiedStore = useUnifiedAudioStore();

const canDisconnect = computed(() =>
  unifiedStore.systemState.source === 'bluetooth' && unifiedStore.systemState.controls.includes('disconnect')
);
</script>
