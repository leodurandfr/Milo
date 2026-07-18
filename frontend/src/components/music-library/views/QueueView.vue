<template>
  <div class="queue-view">
    <MessageContent
      v-if="!store.queue.length"
      icon="queue"
      :title="t('musicLibrary.queueEmpty')"
      :subtitle="t('musicLibrary.queueEmptyHint')"
    />

    <div v-else class="tracks">
      <TrackRow
        v-for="(song, idx) in store.queue"
        :key="`${song.id}-${idx}`"
        :song="song"
        :number="idx + 1"
        :current="idx === store.queueIndex"
        :playing="store.isPlaying"
        show-artist
        @play="store.playIndex(idx)"
      />
    </div>
  </div>
</template>

<script setup>
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import MessageContent from '@/components/ui/MessageContent.vue';
import TrackRow from '../cards/TrackRow.vue';

const { t } = useI18n();
const store = useMusicLibraryStore();
</script>

<style scoped>
.queue-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.tracks {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}
</style>
