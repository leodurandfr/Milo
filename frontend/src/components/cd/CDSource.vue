<!-- CDSource.vue - CD Player (wrapper around AudioPlayerFull) -->
<template>
  <AudioPlayerFull source="cd" :hideContent="cdStore.showTracklist" :hasNext="hasNext">
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
          <div class="tracklist-titles">
            <span class="heading-3 tracklist-artist">{{ artistName }}</span>
            <span class="heading-4 tracklist-album">{{ albumTitle }}</span>
          </div>
          <span v-if="releaseYear" class="text-mono-small tracklist-year">{{ releaseYear }}</span>
        </div>
        <div class="tracklist-scroll">
          <TrackRow v-for="track in cdStore.tracks" :key="track.number" :song="track"
            :number="track.number"
            :current="track.number === cdStore.currentTrack"
            :playing="track.number === cdStore.currentTrack && cdStore.isPlaying"
            :fallback-title="t('audioSources.cdSource.trackN', { n: track.number })"
            @play="cdStore.playTrack($event)" />
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
import TrackRow from '@/components/audio/TrackRow.vue';
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

// Release year (MusicBrainz "YYYY" or empty) — shown next to the album when known
const releaseYear = computed(() => cdStore.discInfo?.year || '');

// Mirrors backend's next_track no-op on the last track.
const hasNext = computed(() =>
  !cdStore.currentTrack || cdStore.currentTrack < cdStore.tracks.length
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
  justify-content: space-between;
  align-items: last baseline;
  gap: var(--space-03);
  padding-top: var(--space-06);
  padding-bottom: var(--space-04);
  flex-shrink: 0;
}

.tracklist-titles {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  min-width: 0;
}

.tracklist-artist {
  color: var(--color-text);
}

.tracklist-album {
  color: var(--color-text-secondary);
}

.tracklist-year {
  flex-shrink: 0;
  white-space: nowrap;
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
