<!-- CDSource.vue - CD Player (wrapper around AudioPlayerFull) -->
<template>
  <div class="cd-player">
    <AudioPlayerFull source="cd">
      <template #action-buttons>
        <div class="action-buttons">
          <IconButton
            :icon="cdStore.showTracklist ? 'close' : 'queue'"
            variant="background-strong"
            size="small"
            @click="cdStore.toggleTracklist()"
          />
          <IconButton
            icon="eject"
            variant="background-strong"
            size="small"
            @click="cdStore.eject()"
          />
        </div>
      </template>
    </AudioPlayerFull>

    <!-- Tracklist overlay -->
    <Transition name="tracklist">
      <div v-if="cdStore.showTracklist" class="tracklist-overlay">
        <div class="tracklist-content">
          <div class="tracklist-header">
            <h2 class="heading-2">{{ albumTitle }}</h2>
            <p class="text-mono-small tracklist-subtitle">{{ artistName }}</p>
          </div>
          <div class="tracklist-scroll">
            <TrackCard
              v-for="track in cdStore.tracks"
              :key="track.number"
              :track="track"
              :isCurrent="track.number === cdStore.currentTrack"
              :isPlaying="track.number === cdStore.currentTrack && cdStore.isPlaying"
              @play="cdStore.playTrack($event)"
            />
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useCdStore } from '@/stores/cdStore';

import AudioPlayerFull from '@/components/audio/AudioPlayerFull.vue';
import IconButton from '@/components/ui/IconButton.vue';
import TrackCard from './TrackCard.vue';

const { t } = useI18n();
const cdStore = useCdStore();

const artistName = computed(() =>
  cdStore.discInfo?.artist || t('audioSources.cdSource.unknownArtist')
);

const albumTitle = computed(() =>
  cdStore.discInfo?.album || t('audioSources.cdSource.unknownAlbum')
);
</script>

<style scoped>
.cd-player {
  width: 100%;
  height: 100%;
  position: relative;
}

/* === ACTION BUTTONS === */
.action-buttons {
  position: absolute;
  top: var(--space-04);
  right: var(--space-04);
  display: flex;
  gap: var(--space-02);
  z-index: 10;
}

/* === TRACKLIST OVERLAY === */
.tracklist-overlay {
  position: absolute;
  top: 0;
  right: 0;
  width: 50%;
  height: 100%;
  z-index: 5;
  background: var(--color-background-neutral);
  display: flex;
  flex-direction: column;
}

.tracklist-content {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: var(--space-06) var(--space-04) var(--space-04) var(--space-04);
}

.tracklist-header {
  text-align: center;
  padding: var(--space-03) 0;
  flex-shrink: 0;
}

.tracklist-subtitle {
  color: var(--color-text-light);
}

.tracklist-scroll {
  flex: 1;
  overflow-y: auto;
  padding-bottom: var(--space-05);
}

/* === TRACKLIST TRANSITION === */
.tracklist-enter-active {
  transition: opacity var(--transition-normal), transform var(--transition-spring);
}

.tracklist-leave-active {
  transition: opacity var(--transition-fast-leave), transform var(--transition-fast-leave);
}

.tracklist-enter-from {
  opacity: 0;
  transform: translateX(var(--space-06));
}

.tracklist-leave-to {
  opacity: 0;
  transform: translateX(var(--space-06));
}

/* === RESPONSIVE (Portrait / Mobile) === */
@media (max-aspect-ratio: 4/3) {
  .tracklist-overlay {
    width: 100%;
  }

  .action-buttons {
    top: var(--space-03);
    right: var(--space-03);
  }
}
</style>
