<!-- CDSource.vue - CD Player (wrapper around AudioPlayerFull) -->
<template>
  <AudioPlayerFull source="cd" :hideContent="cdStore.showTracklist">
    <!-- The tracklist toggle at the start of the player's top row, eject at
         its end. -->
    <template #top-start>
      <IconButton :icon="cdStore.showTracklist ? 'close' : 'queue'" variant="control" size="medium"
        @click="cdStore.toggleTracklist()" />
    </template>
    <template v-if="canEject" #top-end>
      <IconButton icon="eject" variant="control" size="medium" @click="cdStore.eject()" />
    </template>

    <template #content-replace>
      <div class="tracklist-content">
        <div class="tracklist-header">
          <div class="tracklist-titles">
            <span class="heading-3 tracklist-artist">{{ artistName }}</span>
            <span class="heading-4 tracklist-album">{{ albumTitle }}</span>
          </div>
          <span v-if="releaseYear" class="text-mono-medium tracklist-year">{{ releaseYear }}</span>
        </div>
        <div class="tracklist-scroll">
          <TrackRow v-for="track in cdStore.tracks" :key="track.number" :song="trackRecord(track)"
            :number="track.number"
            :current="track.number === cdStore.currentTrack"
            :playing="track.number === cdStore.currentTrack && cdStore.isPlaying"
            :fallback-title="t('audioSources.cdSource.trackN', { n: track.number })"
            @play="playTrack" />
        </div>
      </div>
    </template>
  </AudioPlayerFull>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useCdStore } from '@/stores/cdStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

import AudioPlayerFull from '@/components/audio/AudioPlayerFull.vue';
import IconButton from '@/components/ui/IconButton.vue';
import TrackRow from '@/components/audio/TrackRow.vue';

const { t } = useI18n();
const cdStore = useCdStore();
const unifiedStore = useUnifiedAudioStore();

const artistName = computed(() =>
  cdStore.discInfo?.artist || t('audioSources.cdSource.unknownArtist')
);

const albumTitle = computed(() =>
  cdStore.discInfo?.album || t('audioSources.cdSource.unknownAlbum')
);

// Release year (MusicBrainz "YYYY" or empty) — shown next to the album when known
const releaseYear = computed(() => cdStore.discInfo?.year || '');

// The backend lists what the disc accepts now: eject even for a disc it
// cannot play, play_track only once the disc is playable.
const canEject = computed(() => unifiedStore.systemState.controls.includes('eject'));
const canPlayTrack = computed(() => unifiedStore.systemState.controls.includes('play_track'));

function playTrack(number) {
  if (canPlayTrack.value) cdStore.playTrack(number);
}

// TrackRow reads a catalog record whose duration is in seconds; the wire's is in ms.
function trackRecord(track) {
  return {
    title: track.title,
    duration: track.duration_ms == null ? null : track.duration_ms / 1000,
  };
}
</script>

<style scoped>
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

.tracklist-scroll > :deep(.track-row:last-child) {
  border-bottom-color: transparent;
}

@media (max-aspect-ratio: 4/3) {
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
