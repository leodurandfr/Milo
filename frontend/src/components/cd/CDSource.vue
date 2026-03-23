<!-- CDSource.vue - CD Player (wrapper around AudioPlayerFull) -->
<template>
  <AudioPlayerFull source="cd" :hideContent="cdStore.showTracklist">
    <template #action-buttons>
      <div class="action-buttons">
        <IconButton :icon="cdStore.showTracklist ? 'close' : 'queue'" :variant="isMobile ? 'on-grey' : 'background-strong'"
          size="medium" @click="cdStore.toggleTracklist()" />
        <IconButton icon="eject" :variant="isMobile ? 'on-grey' : 'background-strong'" size="medium"
          @click="cdStore.eject()" />
      </div>
    </template>

    <template #content-replace>
      <div class="tracklist-content">
        <div class="tracklist-header">
          <span class="heading-3 tracklist-artist">{{ artistName }}</span>
          <span class="heading-3 tracklist-album">{{ albumTitle }}</span>
        </div>
        <div class="tracklist-scroll">
          <TrackCard v-for="track in cdStore.tracks" :key="track.number" :track="track"
            :isCurrent="track.number === cdStore.currentTrack"
            :isPlaying="track.number === cdStore.currentTrack && cdStore.isPlaying" @play="cdStore.playTrack($event)" />
        </div>
      </div>
    </template>
  </AudioPlayerFull>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useCdStore } from '@/stores/cdStore';

import AudioPlayerFull from '@/components/audio/AudioPlayerFull.vue';
import IconButton from '@/components/ui/IconButton.vue';
import TrackCard from './TrackCard.vue';
import { useIsMobile } from '@/composables/useIsMobile';

const { t } = useI18n();
const cdStore = useCdStore();
const { isMobile } = useIsMobile();

const artistName = computed(() =>
  cdStore.discInfo?.artist || t('audioSources.cdSource.unknownArtist')
);

const albumTitle = computed(() =>
  cdStore.discInfo?.album || t('audioSources.cdSource.unknownAlbum')
);
</script>

<style scoped>
/* === ACTION BUTTONS === */
.action-buttons {
  display: flex;
  justify-content: space-between;
  flex-shrink: 0;
}

/* === TRACKLIST === */
.tracklist-content {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}

.tracklist-header {
  display: flex;
  align-items: baseline;
  gap: var(--space-03);
  padding-top: var(--space-06);
  padding-bottom: var(--space-04);
  flex-shrink: 0;
}

.tracklist-artist {
  color: var(--color-text);
}

.tracklist-album {
  color: var(--color-text-secondary);
}

.tracklist-scroll {
  flex: 1;
  overflow-y: auto;
  border-top: 1px solid var(--color-border);
  margin-bottom: calc(-1 * var(--space-05));
  padding-bottom: var(--space-05);
}

@media (max-aspect-ratio: 4/3) {
  .action-buttons {
    position: absolute;
    top: calc(max(var(--space-05), env(safe-area-inset-top, 0px)) + var(--space-04));
    left: calc(var(--space-05) + var(--space-04));
    right: calc(var(--space-05) + var(--space-04));
    z-index: 10;
  }

  .tracklist-scroll {
    margin-bottom: calc(-1 * max(var(--space-06), env(safe-area-inset-bottom, 0px)));
    padding-bottom: max(var(--space-06), env(safe-area-inset-bottom, 0px));
  }
}

/* === TRACKLIST STAGGER === */
.tracklist-header,
.tracklist-scroll {
  opacity: 0;
  transform: translateY(var(--space-05));
  animation:
    stagger-in-transform var(--transition-spring) forwards,
    stagger-in-opacity 0.4s ease forwards;
}

.tracklist-header {
  animation-delay: 0ms;
}

.tracklist-scroll {
  animation-delay: 80ms;
}

@keyframes stagger-in-transform {
  to {
    transform: translateY(0);
  }
}

@keyframes stagger-in-opacity {
  to {
    opacity: 1;
  }
}
</style>
